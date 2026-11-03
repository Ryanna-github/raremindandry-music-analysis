# 代码入口

下列命令均从本仓库根目录运行；源码位于 `work/vocal-detail/`，本机输出位于 `outputs/instrument_demo/`。

## 当前网页源码

- `web/waveform3d.js`：真实波形几何、左右峰值合并为一个展示体、同源色相锁定、标签连接线、不累计静音透明度、360° 旋转、已勾选包络叠加与时间轴拖动 / 定点播放。
- `web/stem_mixer.js`：立体声多轨混听、勾选增益总线、共享时间同步、原速 / 保音高调速。
- `web/player.js`：声源层级、当前编辑轨与混听勾选、处理方式、播放 / 定位 / A/B 循环、评分。
- `export_mix.py`：校验组合请求，按试听电平合成立体声 WAV / MP3，生成带源文件校验值的组合清单。
- `web/export_range.js`：导出时间输入解析、毫秒格式化、全片 / A/B / 自定义范围与边界校验。
- `../demucs-detailed/serve_demo.py`：本地网页、音频 Range 请求及异步组合导出 API。
- `web/player.css`：网页布局与样式。
- `player_template.html`：页面模板。
- `processing_defaults.json`：连续人声、播放和视觉默认参数。

## 刷新现有 Demo

在项目根目录运行：

```bash
work/separator-env/bin/python work/vocal-detail/refresh_player.py
```

此入口仅刷新 HTML / assets / manifest，不重新编码现有音频或计算评分。运行目录为 `outputs/instrument_demo/`，当前预览为 `http://127.0.0.1:8874/`。

不要只修改输出目录中的 assets；再次刷新会从 `web/` 源码复制覆盖。

## 启动支持组合导出的服务器

在项目根目录运行；需要 separator-env 内的 FFmpeg 和 soundfile，普通静态服务器仅支持试听，不支持合成导出。

```bash
work/separator-env/bin/python work/demucs-detailed/serve_demo.py
```

仅绑定 `127.0.0.1:8874`；`POST /api/exports` 提交音轨 ID、处理方式、WAV / MP3 与时间范围，`GET /api/exports/<id>` 查看状态。音频和清单保存在 `outputs/instrument_demo/exports/<id>/`，不会改写原轨。一次运行一个任务、另可排队一个任务。

使用与限制见 `COMBINATION_EXPORT.md`。本 demo 子声部已预计算；网页的拆分操作加载全片主唱、合声和观众声候选，不重复运行分离模型。

## 新音频处理

`initialize_audio.py` 是当前两阶段入口：先生成全片人声及配乐基础轨，再按需 `--expand-vocals`。具体分离模型与中间音频在本目录及 `../demucs-detailed/`，不是在浏览器中运行分离。

## 回归检查

```bash
node work/vocal-detail/test_unified_wave.js
node work/vocal-detail/test_stem_mixer.js
node work/vocal-detail/test_transport_races.js
node work/vocal-detail/test_export_range.js
work/separator-env/bin/python work/vocal-detail/test_export_mix.py
```

这些是实际波形数据的几何测试与模拟媒体总线测试；真实浏览器交互另经本地预览验证。
