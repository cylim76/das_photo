# 标注工具

标注工具已经并入 DAS Photo 主入口，推荐通过下载器进入：

```bat
run.bat
```

浏览器地址：

```text
http://127.0.0.1:8787/labeler
```

如需单独启动标注器，也可以运行：

```bat
run_labeler.bat
```

Linux 下：

```bash
chmod +x run_labeler.sh
./run_labeler.sh
```

单独启动时地址为：

```text
http://127.0.0.1:8765/labeler
```

标注器会读取照片根目录，扫描 `年/月/箱号/阶段/图片` 结构，并在照片目录生成 `dataset.json`、`index.json` 和每个箱号下的 `container.json`。
