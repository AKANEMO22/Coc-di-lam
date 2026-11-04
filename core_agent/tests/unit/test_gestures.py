"""Avatar gesture tags: lenient parsing, and tags never reaching the transcript or scoring."""
from pathlib import Path

import pytest

from core.interview.context import ContextAssembler
from core.interview.director import StageDirector
from core.interview.gestures import GestureStream, split_gestures, strip_gestures
from core.interview.queue import InMemoryAssessmentQueue
from core.interview.runner import InteractiveSubagentRunner
from core.interview.speaker import SpeakerContextBuilder
from core.runtime.agent import RuntimeAgent
from core.runtime.providers.mock import MockLLMClient
from core.session.state import LifecycleStatus, SessionStateManager
from core.workspace.session import SessionWorkspace


@pytest.mark.parametrize("raw,text,gestures", [
    ("[smile] Chào bạn. [nod] Mình là Linh.", "Chào bạn. Mình là Linh.", ["smile", "nod"]),
    ("[[nod]] Vâng <think/> {gesture: Smile} tiếp.", "Vâng tiếp.", ["nod", "think", "smile"]),
    ("[Gật đầu] Cảm ơn bạn [smile, write].", "Cảm ơn bạn.", ["nod", "smile", "write"]),
    ("Nguồn [src_1] cho biết [dance] vậy.", "Nguồn cho biết vậy.", []),
    ("Nếu x < 5 và y > 3 thì sao?", "Nếu x < 5 và y > 3 thì sao?", []),
    ("Bạn kể thêm nhé [smi", "Bạn kể thêm nhé", []),
    ("Dòng một [\nDòng hai", "Dòng một [\nDòng hai", []),
])
def test_split_gestures_is_lenient(raw, text, gestures):
    assert split_gestures(raw) == (text, gestures)


def test_stream_holds_tags_split_across_tokens():
    stream = GestureStream()
    out = []
    for token in ["[sm", "ile] Ch", "ào [n", "od]] bạn", ". [thi"]:
        out += stream.feed(token)
        assert not any(isinstance(s, str) and ("[" in s or "]" in s) for s in out)
    out += stream.flush()
    assert "".join(s for s in out if isinstance(s, str)) == " Chào  bạn. "
    assert [s[1] for s in out if not isinstance(s, str)] == ["smile", "nod"]


def test_stream_releases_an_opener_that_is_not_a_tag():
    stream = GestureStream()
    assert stream.feed("a [") == ["a "]
    assert "".join(stream.feed("x" * 60)) == "[" + "x" * 60


@pytest.mark.asyncio
async def test_runner_stores_tag_free_transcript_and_scores_without_tags(tmp_path: Path):
    ws = SessionWorkspace(session_id="sess_g", root_path=tmp_path / "sess_g")
    ws.initialize()
    ws.write_artifact("turns/turn_0_interviewer.json", artifact_type="turn_interviewer", payload={
        "turn_id": "turn_000", "turn_number": 0, "role": "interviewer", "session_id": "sess_g",
        "content": "Bạn giới thiệu nhé?", "content_raw": "[smile] Bạn giới thiệu nhé?", "gestures": ["smile"],
    })
    state_mgr = SessionStateManager()
    director = StageDirector(stages=[{"stage_id": "s1", "target_turns": 3}], state_mgr=state_mgr)
    state_mgr.create_session("sess_g", initial_status=LifecycleStatus.STAGE_ACTIVE, current_stage_id="s1")
    queue = InMemoryAssessmentQueue()
    runner = InteractiveSubagentRunner(
        workspace=ws,
        runtime_agent=RuntimeAgent(client=MockLLMClient(canned_tokens=["[no", "d] Cảm ơn bạn. ", "[think] Vì sao?"]),
                                   workspace=ws),
        context_assembler=ContextAssembler(),
        stage_director=director,
        state_mgr=state_mgr,
        assessment_queue=queue,
    )
    events = [e async for e in runner.run_turn(session_id="sess_g", candidate_answer="Mình là An.")]
    streamed = "".join(e.payload.get("token", "") for e in events if e.type.value == "response_token")
    assert streamed == "[nod] Cảm ơn bạn. [think] Vì sao?"
    assert events[-1].payload["interviewer_response"] == "Cảm ơn bạn. Vì sao?"
    stored = ws.read_json("turns/turn_1_interviewer.json")
    stored = stored.get("payload", stored)
    assert stored["content"] == "Cảm ơn bạn. Vì sao?"
    assert stored["content_raw"] == "[nod] Cảm ơn bạn. [think] Vì sao?" and stored["gestures"] == ["nod", "think"]
    job = await queue.dequeue()
    assert "[" not in job["question"] and job["question"] == "Bạn giới thiệu nhé?"

    # Only the speaker's own history keeps the tags.
    history = SpeakerContextBuilder(ws)._turn_messages(1)
    assert [m["content"] for _, m in history if m["role"] == "assistant"] == [
        "[smile] Bạn giới thiệu nhé?", "[nod] Cảm ơn bạn. [think] Vì sao?"]


def test_reply_of_only_tags_is_empty():
    assert strip_gestures("[nod] [smile]") == ""
