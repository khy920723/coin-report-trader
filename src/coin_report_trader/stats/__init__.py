"""봉 통계의 봉 계산(M1 명세 3.5). 계산만 한다.

`core`와 표준 라이브러리만 import한다. 파일, DB, 네트워크, 시계를 읽지 않는다.
"""

from coin_report_trader.stats.bar_stats import compute_bar_stats

__all__ = ["compute_bar_stats"]
