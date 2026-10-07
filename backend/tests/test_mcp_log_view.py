"""render_logs trims build logs for an agent."""
from app.mcp.log_view import MAX_OUTPUT_CHARS, MAX_TAIL_LINES, render_logs


def _build(*steps, status="failed"):
    """steps: (step_id, step_name, step_status) tuples in one stage."""
    return {
        "number": 7,
        "status": status,
        "stages": [{
            "name": "test",
            "steps": [{"id": sid, "name": name, "status": st} for sid, name, st in steps],
        }],
    }


def _chunk(step_id, step_name, content, stage="test"):
    return {"step_id": step_id, "stage_name": stage, "step_name": step_name, "content": content}


def test_failed_build_shows_only_failed_steps():
    build = _build(("s1", "install", "success"), ("s2", "pytest", "failed"))
    chunks = [_chunk("s1", "install", "installed ok\n"), _chunk("s2", "pytest", "boom\n")]
    text = render_logs(build, chunks)
    assert "Build #7 — status: failed" in text
    assert "Showing failed steps only: pytest" in text
    assert "== test / pytest ==" in text and "boom" in text
    assert "installed ok" not in text


def test_successful_build_shows_all_steps():
    build = _build(("s1", "install", "success"), ("s2", "pytest", "success"), status="success")
    chunks = [_chunk("s1", "install", "a\n"), _chunk("s2", "pytest", "b\n")]
    text = render_logs(build, chunks)
    assert "Showing all steps." in text
    assert text.index("== test / install ==") < text.index("== test / pytest ==")


def test_step_filter_overrides_failed_default():
    build = _build(("s1", "install", "success"), ("s2", "pytest", "failed"))
    chunks = [_chunk("s1", "install", "installed ok\n"), _chunk("s2", "pytest", "boom\n")]
    text = render_logs(build, chunks, step="install")
    assert "installed ok" in text and "boom" not in text


def test_unknown_step_lists_the_real_step_names():
    build = _build(("s1", "install", "success"), ("s2", "pytest", "failed"))
    text = render_logs(build, [], step="nope")
    assert "No step named 'nope'" in text
    assert "install, pytest" in text


def test_tail_keeps_the_last_lines_and_reports_omitted():
    build = _build(("s1", "run", "failed"))
    chunks = [_chunk("s1", "run", "\n".join(f"line {i}" for i in range(10)))]
    text = render_logs(build, chunks, tail_lines=3)
    assert "... 7 earlier line(s) omitted." in text
    assert "line 9" in text and "line 7" in text
    assert "line 6" not in text


def test_tail_lines_is_clamped_to_the_maximum():
    build = _build(("s1", "run", "failed"))
    chunks = [_chunk("s1", "run", "\n".join(f"l{i}" for i in range(MAX_TAIL_LINES + 5)))]
    text = render_logs(build, chunks, tail_lines=10**9)
    assert "... 5 earlier line(s) omitted." in text


def test_multiple_chunks_of_one_step_share_a_heading():
    build = _build(("s1", "run", "failed"))
    chunks = [_chunk("s1", "run", "a\n"), _chunk("s1", "run", "b\n")]
    assert render_logs(build, chunks).count("== test / run ==") == 1


def test_no_output_is_stated():
    build = _build(("s1", "run", "failed"))
    assert "No log output recorded." in render_logs(build, [])


def test_build_without_stages_does_not_crash():
    text = render_logs({"number": 1, "status": "pending", "stages": []}, [])
    assert "Build #1 — status: pending" in text


def test_one_enormous_line_is_capped():
    """A single minified/progress line must not return megabytes to the agent."""
    build = _build(("s1", "run", "failed"))
    chunks = [_chunk("s1", "run", "x" * (MAX_OUTPUT_CHARS * 3) + "THE-END")]
    text = render_logs(build, chunks)
    assert len(text) < MAX_OUTPUT_CHARS + 500
    assert text.endswith("THE-END")
    assert f"truncated to its last {MAX_OUTPUT_CHARS} characters" in text


def test_ansi_colour_codes_and_carriage_returns_are_cleaned():
    build = _build(("s1", "run", "failed"))
    chunks = [_chunk("s1", "run", "\x1b[31mFAILED\x1b[0m test_x\r\n10%\r50%\r100%\n")]
    text = render_logs(build, chunks)
    assert "\x1b" not in text
    assert "FAILED test_x" in text
    assert "100%" in text
