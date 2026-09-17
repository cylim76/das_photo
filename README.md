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

浏览器 SSO 自动登录模块已经内置在本项目的 `actions`、`workflows` 和 `core` 目录中，
不再依赖外部 `gerp-import` 项目，也不需要设置额外的模块路径。

## 数据位置

- 下载器数据库：`data\das_cpm_photos.sqlite3`
- 下载器设置：数据库里的 `config` 表
- 标注器设置：`labeler\data\config.json`
- 训练任务和标签配置：`labeler\data\label_config.json`
- 照片目录默认值：项目目录上一级的 `photos`

数据库路径也可以在下载页面的“设置”中修改。数据库路径修改后需要重启服务；该启动配置保存在
`data/runtime_config.json`。也可以用 `DAS_PHOTO_DB` 环境变量或 `--db` 命令行参数覆盖。

照片目录可以在页面设置里修改。下载器保存目录会同步给标注器的照片根目录。

如果要强制指定照片根目录，可以设置环境变量：

```bash
export DAS_PHOTO_ROOT=/data/rpa/photos
```

Windows 示例：

```bat
set DAS_PHOTO_ROOT=D:\RPA\photos
```

## 本地与远程 API 存储

每套 DAS Photo 都可以作为本地一体实例，也可以作为其他下载电脑的存储服务器。

### 存储服务器（例如 P3）

1. 在下载页面“设置”中选择“本地一体模式”。
2. 设置 P3 本机的照片目录和数据库路径。
3. 打开“API Key”页面，为每台下载电脑生成一个 Key。
4. 使用 `run_server.sh` 启动服务，并确保下载电脑可以访问 `8787` 端口。

存储服务器收到上传后，会先校验文件大小和 SHA-256，再把图片写入临时文件并原子改名，最后更新本机 SQLite。

### 远程下载客户端（例如 Windows VM）

1. 在下载页面“设置”中选择“远程 API 模式”。
2. 填写存储服务器地址，例如 `http://P3-IP:8787`。
3. 填写 P3 生成的 API Key，点击“测试远程连接”。
4. 保存设置后正常启动下载。

远程模式下，下载电脑只负责登录 DAS 和取得图片。图片与箱号元数据通过 API 上传，最终照片目录和业务数据库都由
存储服务器决定，因此不需要共享 SQLite，也不需要在 Windows 和 Linux 之间转换路径。
下载电脑仍保留一个本机 SQLite，用于保存自己的设置、下载任务进度和失败信息；照片与正式箱号数据以存储服务器为准。

API Key 在服务器管理页面中可以随时查看、复制、停用或删除。Key 以避免内网误操作为主要目的；如果服务开放到不可信
网络，应在前面增加 HTTPS 和更严格的访问控制。

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
Environment=DAS_PHOTO_DB=/data/rpa/das_photo/data/das_cpm_photos.sqlite3
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
