# BN OKX Funding Monitor

本地运行的 Binance / OKX USDT 永续资金费差监控面板。后台读取两家交易所公开行情，前端展示同一合约在两边的当前资金费、费差年化、建议做多/做空方向、入场盘口、费用后持有期预估收益，以及手工登记跨所持仓后的实时净利润。

## 安装与启动

在 PowerShell 中执行：

```powershell
cd E:\BN_Stock\monitor
.\setup.ps1
.\start.ps1
```

本机浏览器打开 `http://127.0.0.1:8765`。同一局域网设备可访问 `http://<你的局域网IP>:8765`，例如 `http://192.168.10.219:8765`。

也可以手工创建环境：

```powershell
conda env create -f environment.yml
conda run -n bn-stock-monitor python -m uvicorn app.main:app --host 0.0.0.0 --port 8765 --http h11
```

## 数据说明

- Binance 读取 USD-M 永续公开接口，包括 `premiumIndex`、`bookTicker`、`fundingRate`。
- OKX 读取 USDT SWAP 公开接口，包括 `funding-rate`、`funding-rate-history`、`market/tickers`。
- 方向规则：做空当前资金费年化更高的一边，做多更低的一边，理论资金费收入为两边费率差。
- “预估净收益”包含资金费差、入场价差、两边开平仓手续费和预设滑点。它是情景估算，不是已实现利润。
- 默认按总资金一半放 Binance、一半放 OKX，测算名义本金为 `总资金 / 2 × 杠杆`。
- 默认 `auto` 模式会在实时接口失败时保留上一份真实行情；没有真实行情时才切换到演示数据，并显示黄色警告。演示数据不能用于交易判断。

## 配置

复制 `.env.example` 为 `.env` 可修改服务端数据模式、刷新间隔或交易所 API 地址。费用、监控代码、测算杠杆、资金规模和持有期可直接在网页设置中修改。

本项目只做行情监控和收益测算，不会读取 API 密钥，也不会自动下单或划转资金。
