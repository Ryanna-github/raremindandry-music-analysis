"""Loopback-only audio demo server with byte ranges for browser seeking."""
import functools
import json
import re
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent.parent / "outputs/instrument_demo"
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'vocal-detail'))
from export_mix import MixExports, ExportError
EXPORTS=MixExports(ROOT)


class Handler(SimpleHTTPRequestHandler):
    def json_response(self,status,value):
        data=json.dumps(value,ensure_ascii=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
        self.end_headers();self.wfile.write(data)

    def local_request(self):
        hosts={'127.0.0.1:8874','localhost:8874'}
        return self.headers.get('Host') in hosts and self.headers.get('Origin') in {None,'http://127.0.0.1:8874','http://localhost:8874'}

    def do_POST(self):
        if urlsplit(self.path).path!='/api/exports':
            return self.json_response(404,{'error':'接口不存在。'})
        if not self.local_request():
            return self.json_response(403,{'error':'只接受本地同源请求。'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=16384 or self.headers.get('Content-Type','').split(';')[0]!='application/json':
                raise ExportError('请求体须为不超过 16 KB 的 JSON。')
            payload=json.loads(self.rfile.read(length));job=EXPORTS.submit(payload)
            return self.json_response(202,job)
        except (ExportError,ValueError) as error:
            return self.json_response(400,{'error':str(error)})

    def do_GET(self):
        path=urlsplit(self.path).path
        if path.startswith('/api/exports/'):
            if not self.local_request():
                return self.json_response(403,{'error':'只接受本地同源请求。'})
            job_id=path.removeprefix('/api/exports/')
            result=EXPORTS.get(job_id) if re.fullmatch(r'[a-f0-9]{32}',job_id) else None
            return self.json_response(200,result) if result else self.json_response(404,{'error':'导出任务不存在。'})
        return super().do_GET()

    def send_head(self):
        self.byte_range = None
        value = self.headers.get("Range", "")
        path = Path(self.translate_path(self.path))
        if not value or not path.is_file():
            return super().send_head()
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip())
        if not match:
            return super().send_head()
        size = path.stat().st_size
        start, end = match.groups()
        if start:
            start = int(start)
            end = min(int(end) if end else size - 1, size - 1)
        else:
            start, end = max(0, size - int(end)), size - 1
        if start >= size or end < start:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None
        stream = path.open("rb")
        stream.seek(start)
        self.byte_range = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(self.byte_range))
        self.end_headers()
        return stream

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def copyfile(self, source, outputfile):
        if self.byte_range is None:
            return super().copyfile(source, outputfile)
        remaining = self.byte_range
        while remaining:
            chunk = source.read(min(65536, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8874), functools.partial(Handler, directory=str(ROOT)))
    print("Audio demo: http://127.0.0.1:8874", flush=True)
    server.serve_forever()
