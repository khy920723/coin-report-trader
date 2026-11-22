"""M2 명세 6절의 보고서 알림 시나리오: C29(3.11, 3.12.7).

보고서 알림 탭에 주소를 넣고 보고서가 `OK`인 회차를 돌린다. 가짜 디스코드가 받은 글을 본다. 주소는
실행 중에 이어 붙여 만든다. 값은 모두 만든 값이다.
"""

from pathlib import Path

import pytest
from acceptance.m2_support import AAA, make_rig, webhook_url

from coin_report_trader.core import AlertCategory, ReportState


@pytest.mark.scenario("C29")
def test_c29_the_report_alert_sent_to_discord_has_three_lines_and_no_evidence(
    tmp_path: Path,
) -> None:
    url = webhook_url("r")
    rig = make_rig(tmp_path / "app", saved={AlertCategory.REPORT: url})
    try:
        rig.start()
        rig.round()
        assert rig.reports(AAA)[0].state is ReportState.OK
        assert rig.discord.wait_for(1)
        rig.settle()
        (sent,) = rig.discord.alerts
        assert sent.url == url
        lines = sent.text.splitlines()
        assert lines[0] == "[모의매매] 보고서 알림"
        assert lines[1] == "AAA: 회차 1의 보고서를 받았습니다. 최종 판단: 오를 것."
        assert lines[2] == "AI가 쓴 글입니다. 틀릴 수 있습니다."
        assert len(lines) == 3
        for text in ("검색 근거", "봉 근거", "최종 근거", "까닭", "example.com"):
            assert text not in sent.text  # 근거와 결과 글은 없다
    finally:
        rig.stop()
