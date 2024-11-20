from unittest import mock
from typing import Union, Optional

import pytest
from llama_index.core.workflow.workflow import (
    Workflow,
    Context,
)
from llama_index.core.workflow.decorators import step
from llama_index.core.workflow.errors import WorkflowRuntimeError
from llama_index.core.workflow.events import StartEvent, StopEvent, Event
from llama_index.core.workflow.workflow import Workflow, WorkflowHandler
from llama_index.core.workflow.checkpoint import Checkpoint

from .conftest import OneTestEvent, AnotherTestEvent, DummyWorkflow, LastEvent


@pytest.mark.asyncio()
async def test_collect_events():
    ev1 = OneTestEvent()
    ev2 = AnotherTestEvent()

    class TestWorkflow(Workflow):
        @step
        async def step1(self, _: StartEvent) -> OneTestEvent:
            return ev1

        @step
        async def step2(self, _: StartEvent) -> AnotherTestEvent:
            return ev2

        @step
        async def step3(
            self, ctx: Context, ev: Union[OneTestEvent, AnotherTestEvent]
        ) -> Optional[StopEvent]:
            events = ctx.collect_events(ev, [OneTestEvent, AnotherTestEvent])
            if events is None:
                return None
            return StopEvent(result=events)

    workflow = TestWorkflow()
    result = await workflow.run()
    assert result == [ev1, ev2]


@pytest.mark.asyncio()
async def test_get_default(workflow):
    c1 = Context(workflow)
    assert await c1.get(key="test_key", default=42) == 42


@pytest.mark.asyncio()
async def test_get(ctx):
    await ctx.set("foo", 42)
    assert await ctx.get("foo") == 42


@pytest.mark.asyncio()
async def test_get_not_found(ctx):
    with pytest.raises(ValueError):
        await ctx.get("foo")


@pytest.mark.asyncio()
async def test_legacy_data(workflow):
    c1 = Context(workflow)
    await c1.set(key="test_key", value=42)
    assert c1.data["test_key"] == 42


def test_send_event_step_is_none(ctx):
    ctx._queues = {"step1": mock.MagicMock(), "step2": mock.MagicMock()}
    ev = Event(foo="bar")
    ctx.send_event(ev)
    for q in ctx._queues.values():
        q.put_nowait.assert_called_with(ev)


def test_send_event_to_non_existent_step(ctx):
    with pytest.raises(
        WorkflowRuntimeError, match="Step does_not_exist does not exist"
    ):
        ctx.send_event(Event(), "does_not_exist")


def test_send_event_to_wrong_step(ctx):
    ctx._workflow._get_steps = mock.MagicMock(return_value={"step": mock.MagicMock()})

    with pytest.raises(
        WorkflowRuntimeError,
        match="Step step does not accept event of type <class 'llama_index.core.workflow.events.Event'>",
    ):
        ctx.send_event(Event(), "step")


def test_send_event_to_step(ctx):
    step2 = mock.MagicMock()
    step2.__step_config.accepted_events = [Event]

    ctx._workflow._get_steps = mock.MagicMock(
        return_value={"step1": mock.MagicMock(), "step2": step2}
    )
    ctx._queues = {"step1": mock.MagicMock(), "step2": mock.MagicMock()}

    ev = Event(foo="bar")
    ctx.send_event(ev, "step2")

    ctx._queues["step1"].put_nowait.assert_not_called()
    ctx._queues["step2"].put_nowait.assert_called_with(ev)


def test_get_result(ctx):
    ctx._retval = 42
    assert ctx.get_result() == 42


@pytest.mark.asyncio()
async def test_deprecated_params(ctx):
    with pytest.warns(
        DeprecationWarning, match="`make_private` is deprecated and will be ignored"
    ):
        await ctx.set("foo", 42, make_private=True)


def test_create_checkpoint(workflow: DummyWorkflow):
    incoming_ev = StartEvent()
    output_ev = OneTestEvent()

    ctx = Context(workflow=Workflow)
    ctx_snapshot = ctx.to_dict()
    ctx._create_checkpoint(
        last_completed_step="start_step",
        input_ev=incoming_ev,
        output_ev=output_ev,
    )
    ckpt: Checkpoint = ctx._broker_log[0]
    assert ckpt.input_event == incoming_ev
    assert ckpt.output_event == output_ev
    assert ckpt.last_completed_step == "start_step"
    # should be the same since nothing happened between snapshot and creating ckpt
    assert ckpt.ctx_state == ctx_snapshot


@pytest.mark.asyncio()
async def test_checkpoints_after_successive_runs(workflow: DummyWorkflow):
    num_steps = len(workflow._get_steps())
    num_runs = 2

    ctx = Context(workflow=workflow)
    for _ in range(num_runs):
        handler: WorkflowHandler = workflow.run(ctx=ctx)
        await handler

    assert len(handler.ctx._broker_log) == num_steps * num_runs
    assert [ckpt.last_completed_step for ckpt in handler.ctx._broker_log] == [
        None,
        "start_step",
        "middle_step",
        "end_step",
    ] * num_runs


@pytest.mark.asyncio()
async def test_filter_checkpoints(workflow: DummyWorkflow):
    num_runs = 2
    ctx = Context(workflow=workflow)
    for _ in range(num_runs):
        handler: WorkflowHandler = workflow.run(ctx=ctx)
        await handler

    # filter by last complete step
    steps = ["start_step", "middle_step", "end_step"]  # sequential workflow
    for step in steps:
        checkpoints = ctx.filter_checkpoints(last_completed_step=step)
        assert len(checkpoints) == num_runs, f"fails on step: {step.__name__}"

    # filter by input and output event
    event_types = [StartEvent, OneTestEvent, LastEvent, StopEvent]
    for evt_type in event_types:
        # by input_event_type
        if evt_type != StopEvent:
            checkpoints_by_input_event = ctx.filter_checkpoints(
                input_event_type=evt_type
            )
            assert (
                len(checkpoints_by_input_event) == num_runs
            ), f"fails on {evt_type.__name__}"

        # by output_event_type
        checkpoints_by_output_event = ctx.filter_checkpoints(output_event_type=evt_type)
        assert len(checkpoints_by_output_event) == num_runs, f"fails on {evt_type}"
