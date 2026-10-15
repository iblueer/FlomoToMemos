## Flomo To Memos
### 实现思路
1. 将flomo浮墨导出的数据转成json文件
2. 读取json文件将内容和附件图片等通过API上传到你的memos

### 完成功能
- [x] 📃内容上传
- [x] 🏞️图片上传
- [x] 🔗简单其它附件上传
- [x] 🕒修改memos创建时间与flomo一致
- [ ] 其它功能可以提

### 当前环境
> - python: v3.10+
> - memos: v0.30 / v0.31
> - flomo: HTML 导出

### 版本提醒
memos更新了很多版本,api与鉴权各不相同.
本仓库有不同版本分支,注意对应.

## 使用方法
1. 将flomo导出并下载![img.png](static/img.png)
2. 将下载后的文件放进 `flomo` 文件夹![img_1.png](static/img_1.png)
3. 安装依赖
4. **修改 `memos/api.py` 的 `Host` 参数**
5. **参考[获取Token](https://www.usememos.com/docs/security/access-tokens) 登入你的 memos站点 将 `设置 -> 我的账号 -> token的一个值` 的值全部复制到 `token.txt`** (0.18版本左右更改了验证方式,不要cookies了)
6. 运行 `servicer.py` 生成json
7. 运行 `python3 controller.py --delay 1` 将内容和图片附件上传到 Memos。脚本会保存 `flomo/import-state.json`，中断后可继续运行同一命令。
8. 如何不满意可以运行 `controller.py` 的 `delete()` 方法, 删除上传内容, 再去资源库一键清空未使用图片 , 再**自定义代码** !

## 后续全量导出同步

首次导入完成后，运行一次：

```bash
python3 controller.py --sync
```

这会建立 `flomo/import-manifest.json`。清单只保存来源键与哈希，不保存笔记正文。

以后重新从 flomo 导出完整数据、运行 `servicer.py` 后，先预览差异：

```bash
python3 controller.py --sync
```

预览会分别统计未变更、新增和已编辑的笔记。确认后只上传新增笔记：

```bash
python3 controller.py --sync --apply --delay 1
```

已编辑的旧笔记不会自动覆盖 Memos，避免误改历史内容；脚本会报告数量，等待你决定更新或创建副本。
