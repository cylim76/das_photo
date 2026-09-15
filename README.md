# DAS Photo

DAS 照片下载和集装箱照片标注工具。这个目录可以作为独立项目整体移动，例如移动到：

```bat
D:\RPA\das_photo
```

也可以部署到 Linux 服务器，例如：

```bash
/data/rpa/das_photo
```

## 启动

推荐入口：

```bat
D:\RPA\das_photo\run.bat
```

启动后会自动打开浏览器：

- 下载页面：http://127.0.0.1:8787
- 标注页面：http://127.0.0.1:8787/labeler

也可以单独启动：

```bat
run_downloader.bat
run_labeler.bat
```

Linux 下启动：

```bash
chmod +x run.sh run_downloader.sh run_labeler.sh
./run.sh
```

服务器后台入口：

```bash
chmod +x run_server.sh
./run_server.sh
```

## 安装依赖

如果当前电脑没有 Flask，先在项目目录执行：

```bat
python -m pip install -r requirements.txt
```

启动脚本会优先使用项目内 `.venv\Scripts\python.exe`，没有 `.venv` 时使用系统 `python`。

## 数据位置

- 下载器数据库：`data\das_cpm_photos.sqlite3`
- 下载器设置：数据库里的 `config` 表
- 标注器设置：`labeler\data\config.json`
- 训练任务和标签配置：`labeler\data\label_config.json`
- 照片目录默认值：项目目录上一级的 `photos`

照片目录可以在页面设置里修改。下载器保存目录会同步给标注器的照片根目录。

如果要强制指定照片根目录，可以设置环境变量：

```bash
export DAS_PHOTO_ROOT=/data/rpa/photos
```

Windows 示例：

```bat
set DAS_PHOTO_ROOT=D:\RPA\photos
```

## 标注 JSON

标注 JSON 写在照片目录下，作为主标注格式：

- `dataset.json`
- `index.json`
- 每个箱号目录下的 `container.json`
- `label_config.json`

复制照片目录给其他开发人员时，照片、箱号信息、训练任务、标签配置、标注框、OCR 文本标准答案都会一起带走。YOLO / COCO / OCR 等训练格式以后由导出程序从这些 JSON 生成。

## 移动项目

可以直接把整个 `das_photo` 文件夹复制或移动到 `D:\RPA\das_photo`。移动后：

1. 运行 `D:\RPA\das_photo\run.bat`
2. 到下载器设置里确认照片保存目录
3. 到标注页面设置里扫描照片目录，刷新索引

项目内部路径都按当前文件位置计算，不依赖 `gerp-import`，也不依赖项目目录必须叫 `das_photo`。

## systemd 服务

Linux 服务器上建议用 `run_server.sh` 作为 systemd 入口。它不会自动打开浏览器，只负责启动 Web 服务。

示例服务文件：

```ini
[Unit]
Description=DAS Container Photo Tool
After=network.target

[Service]
Type=simple
WorkingDirectory=/data/rpa/container_photo_tool
ExecStart=/data/rpa/container_photo_tool/run_server.sh
Restart=always
RestartSec=5
Environment=DAS_PHOTO_ROOT=/data/rpa/photos
Environment=DAS_PHOTO_HOST=0.0.0.0
Environment=DAS_PHOTO_PORT=8787

[Install]
WantedBy=multi-user.target
```

常用命令：

```bash
sudo systemctl daemon-reload
sudo systemctl enable das-photo
sudo systemctl start das-photo
sudo systemctl stop das-photo
sudo systemctl restart das-photo
sudo systemctl status das-photo
journalctl -u das-photo -f
```
