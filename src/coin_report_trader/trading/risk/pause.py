"""멈춤 상태(M3 명세 3.10, 제품 규칙 11절).

`PauseState`와 그것을 바꾸는 함수, 허락을 묻는 함수다. 계산만 한다. `app`은 바뀐 상태를 표
`pauses`에 쓰고, 켤 때 읽어 이어 간다.

- 사용자 멈춤('멈춤')과 오류 멈춤은 따로다. 둘이 함께 있을 수 있다.
- 이미 그 멈춤이면 같은 값이다. `enter_error`는 이미 오류 멈춤이면 원인을 바꾸지 않는다(처음
  원인이 남는다).
- 회차는 둘 다 아닐 때만 돈다. 손절 점검은 오류 멈춤이 아니고, 사용자 멈춤이면 '손절 계속'일
  때만 한다. 공개 시세 요청은 원인이 "일시 차단"(`EXCHANGE`)인 오류 멈춤이면 하지 않는다.
  전량 매도는 오류 멈춤이 아닐 때만 한다.
"""

from dataclasses import dataclass, replace

from coin_report_trader.core.trade import ErrorPauseCause, PauseStopLoss


@dataclass(frozen=True)
class PauseState:
    user: bool = False
    """사용자 멈춤."""
    error: bool = False
    """오류 멈춤."""
    error_cause: ErrorPauseCause | None = None
    error_detail: str | None = None
    """거래소 오류면 그 오류 이름({오류 종류})."""


def pause_user(state: PauseState) -> PauseState:
    """'멈춤'(3.10.1). 오류 멈춤은 그대로다."""
    return replace(state, user=True)


def release_user(state: PauseState) -> PauseState:
    """'멈춤 풀기'. 오류 멈춤이 남아 있으면 그것은 그대로다."""
    return replace(state, user=False)


def enter_error(state: PauseState, cause: ErrorPauseCause, detail: str | None) -> PauseState:
    """오류 멈춤에 들어간다(3.10.2). 이미 오류 멈춤이면 처음 원인이 남는다. 사용자 멈춤은 그대로."""
    if state.error:
        return state
    return replace(state, error=True, error_cause=cause, error_detail=detail)


def release_error(state: PauseState) -> PauseState:
    """'오류 멈춤 풀기'. 사용자 멈춤은 그대로다."""
    return replace(state, error=False, error_cause=None, error_detail=None)


def may_run_round(state: PauseState) -> bool:
    """회차를 돌려도 되는지: 사용자 멈춤도 오류 멈춤도 아니다."""
    return not state.user and not state.error


def may_stop_check(state: PauseState, pause_stop_loss: PauseStopLoss) -> bool:
    """1분 점검(손절)을 해도 되는지: 오류 멈춤이 아니고, 사용자 멈춤이면 '손절 계속'일 때만."""
    if state.error:
        return False
    return not state.user or pause_stop_loss is PauseStopLoss.KEEP


def may_fetch_quotes(state: PauseState) -> bool:
    """공개 시세를 요청해도 되는지: 원인이 일시 차단(`EXCHANGE`)인 오류 멈춤이 아니다."""
    return not (state.error and state.error_cause is ErrorPauseCause.EXCHANGE)


def may_sell_all(state: PauseState) -> bool:
    """'멈추고 전량 매도'를 해도 되는지: 오류 멈춤이 아니다(사용자 멈춤이어도 된다)."""
    return not state.error


def stop_loss_off(state: PauseState, pause_stop_loss: PauseStopLoss) -> bool:
    """상태 띠의 "지금 손절이 동작하지 않음": `not may_stop_check`."""
    return not may_stop_check(state, pause_stop_loss)
