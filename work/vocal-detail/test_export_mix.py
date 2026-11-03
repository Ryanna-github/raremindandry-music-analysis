"""Real-source stereo export checks, plus API payload and offset validation."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from export_mix import ExportError, plan_export, render_mix, safe_source

ROOT = Path(__file__).resolve().parents[2] / 'outputs/instrument_demo'
REPORT = json.loads((ROOT / 'manifest.json').read_text())
PAYLOAD = {'tracks': [{'id': 'vocal_lead', 'mode': 'continuous'},
                      {'id': 'vocal_backing', 'mode': 'enhanced'},
                      {'id': 'instrument_guitar', 'mode': 'raw'}],
           'vocal_expanded': True, 'format': 'wav', 'range': [68, 75]}


class CombinationExportTests(unittest.TestCase):
    def test_fractional_and_minimum_time_range(self):
        plan = plan_export(REPORT, ROOT, dict(PAYLOAD, range=[68.25, 75.75]))
        self.assertEqual(plan['range'], [68.25, 75.75])
        self.assertEqual(plan['duration'], 7.5)
        plan_export(REPORT, ROOT, dict(PAYLOAD, range=[68.25, 68.30]))
        with self.assertRaises(ExportError):
            plan_export(REPORT, ROOT, dict(PAYLOAD, range=[68.25, 68.299]))

    def test_invalid_selections(self):
        bad = [
            {'tracks': []},
            {'tracks': [PAYLOAD['tracks'][0]] * 2},
            {'tracks': [{'id': 'vocal_original_mix', 'mode': 'raw'}]},
            {'tracks': [{'id': 'vocal_lead', 'mode': 'not_a_mode'}]},
            {'tracks': [{'id': 'vocal_all'}, {'id': 'vocal_lead'}]},
            {'tracks': [{'id': 'vocal_lead', 'path': '/tmp/custom.wav'}]},
            {'range': [-1, 75]}, {'range': [68, 1000]},
            {'range': [68, 68]}, {'range': [68, float('nan')]},
            {'range': [True, 75]}, {'vocal_expanded': 'true'},
            {'format': 'flac'}, {'path': '/tmp/custom.wav'},
        ]
        for change in bad:
            with self.subTest(change=change), self.assertRaises(ExportError):
                plan_export(REPORT, ROOT, dict(PAYLOAD, **change))
        with self.assertRaises(ExportError):
            safe_source(ROOT, '../../work/vocal-detail/player_template.html')

    def test_wav_matches_selected_stereo_sources(self):
        plan = plan_export(REPORT, ROOT, PAYLOAD)
        self.assertEqual(plan['visible_lane_count'], 8)
        self.assertAlmostEqual(plan['mix_gain'], .85 / 8)
        self.assertTrue(all(e['lossless_input'] for e in plan['entries']))
        with tempfile.TemporaryDirectory(prefix='stem-export-test-') as task_dir:
            output, manifest, metadata = render_mix(plan, Path(task_dir) / 'wav')
            info = sf.info(output)
            self.assertEqual((info.frames, info.channels, info.samplerate, info.subtype),
                             (308700, 2, 44100, 'PCM_24'))
            actual, _ = sf.read(output, dtype='float64', always_2d=True)
            expected = np.zeros_like(actual)
            for entry in plan['entries']:
                chunk, _ = sf.read(entry['path'], start=68 * 44100,
                                   frames=7 * 44100, dtype='float64', always_2d=True)
                expected += chunk * entry['listening_level_correction'] * plan['mix_gain']
            maximum_error = float(np.max(np.abs(actual - expected)))
            self.assertLess(maximum_error, 4e-7)
            self.assertGreater(float(np.max(np.abs(actual[:, 0] - actual[:, 1]))), .001)
            recipe = json.loads(manifest.read_text())
            self.assertEqual(recipe['selection'], PAYLOAD['tracks'])
            self.assertEqual(recipe['speed'], 1.0)
            self.assertTrue(all(len(e['source_sha256']) == 64 for e in recipe['entries']))
            self.assertEqual(metadata['output_frames'], info.frames)
            print(json.dumps({'wav_frames': info.frames, 'channels': info.channels,
                              'max_source_mix_error': maximum_error}))

    def test_mp3_render(self):
        plan = plan_export(REPORT, ROOT, dict(PAYLOAD, format='mp3'))
        with tempfile.TemporaryDirectory(prefix='stem-export-test-') as task_dir:
            output, _, _ = render_mix(plan, Path(task_dir) / 'mp3')
            info = sf.info(output)
            self.assertEqual((info.channels, info.samplerate), (2, 44100))
            self.assertAlmostEqual(info.duration, 7, delta=.04)

    def test_delayed_partial_track_alignment_and_padding(self):
        with tempfile.TemporaryDirectory(prefix='stem-export-offset-') as task_dir:
            root = Path(task_dir)
            rate = 44100
            signal = np.column_stack([np.full(rate, .2), np.full(rate, -.1)])
            sf.write(root / 'source.wav', signal, rate, subtype='FLOAT')
            report = {'hierarchy': {'primary_ids': ['vocal_all', 'part'],
                                   'vocal_root_id': 'vocal_all', 'vocal_children': []},
                      'tracks': [{'id': 'vocal_all', 'label': 'Parent', 'duration': 3,
                                  'video_start_seconds': 0, 'audio': {'raw': 'source.wav'}},
                                 {'id': 'part', 'label': 'Part', 'duration': 1,
                                  'video_start_seconds': 1.25, 'audio': {'raw': 'source.wav'}}]}
            plan = plan_export(report, root, {'tracks': [{'id': 'part', 'mode': 'raw'}],
                                             'range': [1, 3]})
            output, _, _ = render_mix(plan, root / 'result')
            actual, _ = sf.read(output, always_2d=True)
            self.assertEqual(actual.shape, (2 * rate, 2))
            self.assertLess(np.max(np.abs(actual[:11025])), 1e-7)
            np.testing.assert_allclose(actual[11025:55125], signal * .85 / 2, atol=2e-7)
            self.assertLess(np.max(np.abs(actual[55125:])), 1e-7)


if __name__ == '__main__':
    unittest.main(verbosity=2)
