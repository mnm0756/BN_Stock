# BN Stock Carry Monitor

本地运行的 Binance 美股现货 / TradFi 合约资金费监控面板。后台读取 Binance USDⓈ-M 公开行情，前端展示当前资金费、最近 7 日简单年化、入场价差、费用后持有期预估收益，以及手工登记套利持仓后的实时净利润。

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

- 合约行情、资金费和历史资金费来自 Binance USDⓈ-M 公开接口。
- Binance 美股现货目前没有稳定的公开 API。机会表使用合约返回的 `indexPrice` 作为参考现货价，并在页面明确标记。
- “预估净收益”包含资金费、入场价差、开平仓手续费和预设滑点。它是情景估算，不是已实现利润。
- “持仓净利润”使用手工登记的两条腿成交价、累计已收资金费和当前价格计算。可填写真实现货价覆盖指数参考价。
- 默认 `auto` 模式会在 Binance 被网络或地区限制时切换到演示数据，并显示黄色警告。演示数据不能用于交易判断。

## 配置

复制 `.env.example` 为 `.env` 可修改服务端数据模式、刷新间隔或 Binance 合约 API 地址。费用、监控代码、资金分配和持有期可直接在网页设置中修改。

本项目只做行情监控和收益测算，不会读取 API 密钥，也不会自动下单或划转资金。
