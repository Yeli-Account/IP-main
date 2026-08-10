# 自动解析目标网站 IP 并更新 A 记录

自动解析某个使用 Cloudflare CDN 的网站（如 `www.shopify.com`）的 IP，把这些 IP 写入你指定域名的 A 记录，实现"借用"其边缘 IP 的效果。

## 原理

1. 通过公共 DNS over HTTPS（1.1.1.1 / dns.google）解析 `CF_TARGET` 填的域名，拿到它的全部 A 记录 IP。
2. 先清空目标记录名下现有的 A 记录，再把这些 IP 全部加进去。
3. 定时自动运行（默认每小时），也可手动触发。

## Fork 即用步骤

1. 点右上角 **Fork** 把本仓库复制到你自己的账号。
2. 在你 fork 的仓库里进入 **Settings → Secrets and variables → Actions**，添加下面的 secret。
3. 进 **Actions** 页，确认 `Update Cloudflare DNS` 工作流已启用。
4. 可手动点一次 **Run workflow** 验证，之后每小时自动运行。

## 配置

在仓库 Settings → Secrets and variables → Actions 添加 secret：

| Secret 名 | 值 |
| --- | --- |
| `CF_API_TOKEN` | Cloudflare API Token，权限勾选 Zone.DNS → Edit，范围限你的域名 |
| `CF_ZONE_ID` | 域名的 Zone ID（Cloudflare 控制台 → 域名 → 概览页右侧） |
| `CF_RECORD` | 写入哪个域名，填 `#` 表示用域名根，必填 |
| `CF_PROXY` | 填 `#` 保持灰云（DNS only），填 `*` 变成橙云（走代理） |
| `CF_TARGET` | 要扒取 IP 的网站，填域名即可，如 `www.shopify.com` |

`CF_TARGET` 支持各种写法，都会自动取域名部分：

- `www.shopify.com`
- `https://www.shopify.com`
- `https://www.shopify.com/path?query=1`

## 使用

- 定时：每小时自动跑一次。
- 手动：Actions → Update Cloudflare DNS → Run workflow。
- 运行日志可在 Actions 里查看，能看到解析出哪些 IP、加了哪几条记录。

## 注意

- 目标域名解析到的 IP 可能变更，定时运行就是为了一直跟着更新。
- 把别的网站 IP 指到自己域名下属于灰色用法，仅供技术学习，风险自负。