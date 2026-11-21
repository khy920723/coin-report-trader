"""봉 통계와 옛 기간별 통계·지표의 봉 계산(M1 명세 3.5, 3.6). 계산만 한다.

`core`와 표준 라이브러리만 import한다. 파일, DB, 네트워크, 시계를 읽지 않는다. 옛
`CandleStats`와 `compute_candle_stats`는 51번까지 그대로 두고 새 `compute_bar_stats`를 더한다.
"""

from coin_report_trader.stats.bar_stats import compute_bar_stats
from coin_report_trader.stats.compute import CandleStats, compute_candle_stats

__all__ = ["CandleStats", "compute_bar_stats", "compute_candle_stats"]
