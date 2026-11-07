import pytest

from app.core import db, state


def test_set_status_working_progress():
    st = state.set_status("payroll", "working", progress=40, current_step="급여대장 15명 중 6명 계산 중",
                          client_name="(주)가나상사")
    assert (st.status, st.progress, st.client_name) == ("working", 40, "(주)가나상사")
    assert state.get_state("payroll").current_step.startswith("급여대장")


def test_idle_clears_client_and_touch_message_keeps_status():
    state.set_status("payroll", "working", client_name="(주)가나상사")
    state.touch_message("payroll", "마지막 메시지")
    st = state.get_state("payroll")
    assert (st.status, st.last_message) == ("working", "마지막 메시지")
    assert state.set_status("payroll", "idle").client_name == ""


def test_progress_is_clamped():
    assert state.set_status("payroll", "working", progress=250).progress == 100
    assert state.set_status("payroll", "working", progress=-5).progress == 0


def test_idle_resets_progress_and_task():
    state.set_status("payroll", "working", progress=60, task_id=7)
    st = state.set_status("payroll", "idle")
    assert (st.progress, st.task_id) == (0, None)


def test_error_reason_only_on_error():
    st = state.set_status("vat", "error", error_reason="주민번호 누락 2건")
    assert st.error_reason == "주민번호 누락 2건"
    assert state.set_status("vat", "idle").error_reason is None


def test_invalid_status_rejected():
    with pytest.raises(ValueError):
        state.set_status("payroll", "sleeping")


def test_only_transitions_are_logged():
    state.set_status("payroll", "working", progress=10)
    state.set_status("payroll", "working", progress=50)  # 같은 상태 → 이력 추가 없음
    state.set_status("payroll", "review")
    logs = db.list_status_log("payroll")
    assert [l["status"] for l in logs] == ["review", "working"]
