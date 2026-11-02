# BillBill

离线读取不同银行、不同来源的账单文件，按你设定的模板整理成一份记账 Excel。所有处理都在本机完成，账单不出网。

**English:** An offline desktop tool for importing, categorizing, and deduplicating financial statements from multiple sources, then exporting them to Excel. Optional local AI models help classify transactions and read scanned statements.

## 怎么用

1. 打开程序，把账单文件或整个文件夹拖进窗口。
2. 看左边每份账单的状态，处理「待确认」的记录：在表格里直接改分类，同一商户以后不用再改。
3. 点「导出 Excel」。

左下角的齿轮是设置，扫帚是清空账本。语言（中文 / English）和外观（跟随系统 / 白天 / 黑夜）在「设置 → 通用」里。

第一次用时，到「设置 → 记账模板」选你自己的 Excel，程序按表头猜每列放什么，猜错的在下拉框里改；不选也有一份内置模板。导出时保留模板的格式和公式。

命令行也能用：

```powershell
billbill.exe --convert 输出.xlsx 账单文件夹
```

## 支持什么文件

| 类型 | 文件 |
|---|---|
| PDF | 文字版；扫描件和图片页需要视觉模型 |
| Word | `.docx`、`.doc`、`.rtf`、`.mht` |
| Excel | `.xlsx`、`.xlsm`、`.xls`（含多个工作表和嵌入图片） |
| 文本 | `.csv`、`.tsv`、`.txt`（含 GBK、定宽排版）、网页表格 |
| 图片 | `.png`、`.jpg`、`.webp`、`.bmp`、`.tif`，需要视觉模型 |

文件类型看内容，不看扩展名：银行导出的「.xls」其实是网页也能读。有密码的 PDF 会弹窗询问。

## 怎么做到遇到新格式也能认

不靠给每家银行写规则。认列分三层，前两层不需要模型：

1. **表头关键词**：中英文的常见说法。
2. **单元格内容**：日期样、金额样的格子落在哪几列。表头是别的语言、甚至没有表头也能认。
3. **算术**：上笔余额 ± 本笔金额 = 本笔余额。这条等式成立的列分配就是对的，三列都是金额时靠「总额 + 手续费 = 净额」找出实际发生额。

PDF 不按列间空隙切列（真实账单的列间距可以只有几 pt），而是先找金额右边缘对齐的位置，文字挤在一起也不影响。

这三层都认不出时才问本地模型，而且只问「哪一列是什么」。**金额和日期永远由代码解析，模型碰不到。**

## 每份账单都会核对

| 状态 | 含义 |
|---|---|
| 已核对 | 每笔都满足余额等式，并和账单印的期初、期末余额对上 |
| 有差异 | 有几处对不上。「余额对不上」筛选里能看到是哪几笔 |
| 无法核对 | 账单没有余额列（支付宝、微信常见） |
| 本期无交易 | 账单自己写明了，或印着合计为 0、期初期末相等 |
| 没读出来 | 说明原因，不影响同一批的其他文件 |

解析错了应该被看见，而不是悄悄进账本。

## 归类

- **分类**：你写的关键词 → 内置商户表 → 记住的判断 → 本地模型 → 待确认。你手动改过的不会被覆盖。
- **本人互转**：账单抬头的账号自动记下，只在流水里露过面的账户和你的姓名写在「设置 → 本人账户」里。只有本人账户之间的互转不算收入和支出，转给别人的钱是真实支出。想让所有写着「转账」的记录都不计，打开「设置 → 通用」里的开关。模型不参与这件事。
- **去重**：导出区间重叠的文件只算一次；微信、支付宝付款和银行扣款的同一笔只保留明细更全的。被排除的记录不删除，随时能恢复。

## 本地模型（可选）

不配模型也能用，表格类账单不需要它。在「设置 → 本地模型」选 GGUF 文件：文字模型用来归类没见过的商户，视觉模型加配套 mmproj 用来读扫描件。程序内置 llama.cpp，不需要装别的。

## 数据放在哪

`data/` 和 `templates/` 都在 exe 旁边（macOS 在 `~/Library/Application Support/BillBill/`），整个目录带走就是搬家。`BILLBILL_HOME` 可以改位置。这些是私人数据，不要提交到 GitHub，`.gitignore` 已经排除。

## 开发

```powershell
.\start.ps1      # 从源码运行
.\build.ps1      # 打包成 dist\billbill.exe
python -m unittest discover -s tests -t tests -p "test_*.py"
```

```
app.py                入口：窗口 / --convert / --selftest
billbill/
  documents.py        任何文件 → 表格
  statement.py        表格 → 交易：认列、定方向、核对
  clean.py            拆商户、对手账户
  classify.py         本人互转、分类、标签
  ledger.py           SQLite 账本、去重
  template.py         模板配置、导出 Excel
  i18n.py             界面语言
  model.py            本地模型
  pipeline.py         串起来
  ui.py, ui_settings.py
```

改解析逻辑后要拿真实账单验证：合成用例写得再全也代表不了真实排版。余额核对就是现成的验收标准。

`tests/fixtures/` 里是用真 Word、Excel 存出来的合成账单。GitHub Actions 的 **Build desktop packages** 在三个平台上测试、打包，并用打包产物实际跑一遍这些文件。
