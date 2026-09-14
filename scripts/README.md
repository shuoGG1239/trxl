# 大文件 Git 传输工具

将大文件拆成伪装成 Next.js webpack 缓存的小文件，通过 GitHub 传输，支持断点续传。

## 原理

| 步骤 | 说明 |
|------|------|
| 分片 | 源文件切成 5MB 小块，包装成 `.next/cache/webpack/client-development/*.pack` |
| 清单 | 元数据写入 `.next/cache/webpack/.meta/client-development.json`（含 SHA-256） |
| 上传 | 每 60 个文件一批 commit + push，进度记录在 `scripts/.xfer_state.json` |
| 下载 | `git clone` / `git pull` 天然支持 HTTP 断点续传 |
| 拼合 | 校验每个分片哈希后按序合并，输出文件再校验总哈希 |

## 限制

- GitHub 单文件上限 **100 MB**，默认 5 MB 分片安全
- 约 4 GB 文件会产生 ~800 个分片、仓库约 4+ GB，推送/克隆较慢但可行
- 建议网络不稳定时减小 `--batch-size`，避免单次 push 过大

---

## 发送端（本机）

### 1. 分片

```powershell
python scripts/split.py "I:\UnitySetup64-6000.3.10f1.exe"
```

可选参数：

```powershell
# 每片 8 MB（更少文件数，但单文件更大）
python scripts/split.py "I:\UnitySetup64-6000.3.10f1.exe" --chunk-size 8

# 覆盖已有分片
python scripts/split.py "I:\UnitySetup64-6000.3.10f1.exe" --force
```

### 2. 分批推送（可反复执行）

```powershell
python scripts/push_chunks.py
```

网络中断后直接**重新运行同一命令**，会从上次成功的批次继续。

```powershell
# 预览计划
python scripts/push_chunks.py --dry-run

# 每批 40 个文件（网络差时推荐）
python scripts/push_chunks.py --batch-size 40
```

### 3. 首次还需提交项目骨架

分片脚本不会自动提交 Next.js 骨架，首次需要：

```powershell
git add package.json next.config.js tsconfig.json src/ .gitignore README.md scripts/split.py scripts/push_chunks.py scripts/assemble.py scripts/README.md
git commit -m "init: next.js starter"
git push origin main
```

之后再运行 `push_chunks.py`。

---

## 接收端（另一台机器）

只需 Python 3.8+ 和 Git，**不需要** Node.js。

### 方式 A：一键脚本

```bash
python assemble.py --repo https://github.com/shuoGG1239/trxl.git --output UnitySetup64-6000.3.10f1.exe
```

### 方式 B：手动 clone 再拼合

```bash
git clone https://github.com/shuoGG1239/trxl.git
cd trxl
python scripts/assemble.py --repo . --output ../UnitySetup64-6000.3.10f1.exe --skip-clone
```

### 断点续传

| 场景 | 操作 |
|------|------|
| `git clone` 中断 | 删除不完整目录后重试，或 `cd trxl && git fetch && git checkout main` |
| `git pull` 中断 | 重新 `git pull`，Git 会续传未完成的对象 |
| 拼合前缺分片 | 重新运行 `assemble.py`，自动 `git checkout` 缺失文件 |
| 拼合中断 | 重新运行，`.part` 临时文件会重建；若输出文件已完整且哈希正确则跳过 |

```bash
# 网络恢复后
cd trxl && git pull
python assemble.py --repo . --output ../UnitySetup64-6000.3.10f1.exe --skip-clone
```

---

## 目录结构（伪装后）

```
trxl/
├── package.json
├── src/app/...
├── .next/cache/webpack/
│   ├── .meta/client-development.json   ← 清单（伪装成缓存元数据）
│   └── client-development/
│       ├── 0.pack                      ← 分片（伪装成 webpack pack）
│       ├── 1.pack
│       └── ...
└── scripts/
    ├── split.py
    ├── push_chunks.py
    └── assemble.py
```

## 故障排查

**push 失败 `remote: error: File is xxx MB`**  
→ 减小 `--chunk-size`（如 3），重新 `--force` 分片

**clone 极慢**  
→ 正常，4 GB 仓库需要时间；可开代理或多次 pull

**assemble 报 hash mismatch**  
→ 分片损坏，删除对应 `.pack` 后 `git checkout HEAD -- <path>` 重新拉取

**想清理仓库**  
→ 删除所有 `.pack` 文件并 push 新 commit（Git 历史仍占空间，需 `git filter-repo` 才能彻底清理）
