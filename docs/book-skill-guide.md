# Book Skill：搜书与下载

输入书名，选择版本和格式，再下载。下面用《小王子》演示。

## 使用前准备

| 准备项 | 要求 |
| --- | --- |
| Codex | 能加载技能，并已连接可操作网页的浏览器工具 |
| GPT | 支持工具调用和看图，可从 `gpt-6.1-sol` 开始 |
| 账号 | 已配置可用密钥；参考 [Codex 配置教程](https://doc.senmora.top:38483/docs/cc-switch-codex/) |
| 网络 | 电脑能访问 GitHub、Anna’s Archive 和下载页面 |

**模型能聊天，不代表浏览器能访问书站。** 如需代理，也要让浏览器使用它。

## 1. 安装技能

把这句话发给 Codex：

```text
使用 $skill-installer 安装 https://github.com/StealHD/gitSkills/tree/main/skills/book-skill
```

安装后确认技能列表中有 `book-skill`；没出现时重启 Codex。

## 2. 搜书，选择格式

```text
用 book-skill 搜《小王子》，列出不同格式，先不下载。
```

![小王子搜索结果：EPUB 与 MOBI 两种格式，尚未下载](https://raw.githubusercontent.com/StealHD/gitSkills/main/docs/assets/book-skill/search-results.png)

实测结果：先看版本和格式，再选 A1 或 A2。[查看大图](https://raw.githubusercontent.com/StealHD/gitSkills/main/docs/assets/book-skill/search-results.png)

想要特定译本、中文 EPUB 或 PDF，直接在请求中写清楚。只搜书不会自动保存文件。

## 3. 下载选中的书

```text
下载上面的 A1，保存到我的下载目录。
```

下载并校验通过后，会返回文件链接。多本书可以说：“下载这三本并打包”。

![小王子下载复测成功，文件校验通过](https://raw.githubusercontent.com/StealHD/gitSkills/main/docs/assets/book-skill/download-retest.png)

这是一次下载复测记录；5.46 秒仅为该文件的传输时间。[查看大图](https://raw.githubusercontent.com/StealHD/gitSkills/main/docs/assets/book-skill/download-retest.png)

## 遇到问题

<details>
<summary>环境、模型和网络怎么准备？</summary>

- **浏览器工具**：需要能打开网页、看截图、点击和保存文件。缺少工具时先完成客户端配置，单独安装 Skill 不会装好浏览器工具。
- **模型选择**：日常可用 Sol；需要更多看图操作可试 Terra。以账号实际可用的模型为准，识别验证码还需要图片输入能力。[官方模型说明](https://developers.openai.com/codex/models)
- **网络与代理**：模型 API 和浏览器访问是两条链路。CC Switch 配置密钥不会自动给浏览器配置代理；搜索能打开，也要确认下载页面可访问。
- **运行位置**：本次实测为 macOS + Chrome。远程或云端运行时，文件通常保存在对应工作区。

</details>

<details>
<summary>验证码、链接过期或下载失败怎么办？</summary>

| 情况 | 怎么做 |
| --- | --- |
| 出现验证码 | Skill 会尝试正常界面操作；需要本人协助时，按提示完成后继续 |
| 下载链接过期 | 点击结果中的“刷新入口”，或让 Codex 刷新链接 |
| 网站限流或下载失败 | 按提示等待后再继续，避免反复点击下载 |
| 文件校验失败 | 先暂停使用该文件，把报错交给 Codex 排查 |
| 没有想要的版本 | 补充译者、出版社或 ISBN 后再搜 |

</details>

适用版本：v0.2.5 · [技能下载](https://github.com/StealHD/gitSkills/releases/tag/book-skill/v0.2.5) · [源码](https://github.com/StealHD/gitSkills/tree/main/skills/book-skill)
