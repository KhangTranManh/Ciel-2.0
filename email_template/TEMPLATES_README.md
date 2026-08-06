# Email HTML Templates

Reusable HTML report layouts used by Ciel for styled email sends.
Placeholders use the {{TOKEN}} format and are filled ONLY with real tool data.

| File | Report type | Filled by |
|------|-------------|-----------|
| market_report.html | BTC & XAU/USD dashboard (Report.pdf layout) | build_market_report_html (trading_ops.py) |
| analysis_report.html | Generic file/text/image analysis | build_analysis_report_html (report_ops.py) |
| health_report.html | Health / fitness summary | future health tool |

Report.pdf is the ORIGINAL visual reference (social analytics). market_report.html
remaps that dashboard layout to market data. Add a new *.html here per report type.
