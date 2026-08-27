"""v2 能力核心层契约测试(不依赖 LLM,不依赖任何基座)。"""

from __future__ import annotations

import pytest

from data_analysis_agent.capabilities import (
    CapabilityError,
    CapabilityOutput,
    CapabilityRegistry,
    CapabilitySpec,
    OutputKind,
    Permission,
)


def _spec(name: str = "demo_echo") -> CapabilitySpec:
    return CapabilitySpec(
        name=name,
        description="demo capability for tests",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        domain="demo",
    )


class TestSpecValidation:
    def test_rejects_bad_name(self) -> None:
        with pytest.raises(ValueError, match="capability name invalid"):
            _spec(name="Bad-Name")

    def test_rejects_non_object_schema(self) -> None:
        with pytest.raises(ValueError, match="JSON object schema"):
            CapabilitySpec(
                name="demo_x",
                description="d",
                input_schema={"type": "string"},
                domain="demo",
            )

    def test_rejects_unknown_error_code(self) -> None:
        with pytest.raises(ValueError, match="unknown error code"):
            CapabilitySpec(
                name="demo_x",
                description="d",
                input_schema={"type": "object"},
                domain="demo",
                error_codes=("nope",),
            )

    def test_public_dict_roundtrip(self) -> None:
        spec = _spec()
        public = spec.to_public_dict()
        assert public["name"] == "demo_echo"
        assert public["permission"] == Permission.READ_ONLY.value
        assert public["output_kind"] == OutputKind.TEXT.value


class TestRegistry:
    async def test_execute_success_envelope(self) -> None:
        registry = CapabilityRegistry()

        async def handler(inputs: dict[str, object]) -> CapabilityOutput:
            return CapabilityOutput(content=str(inputs.get("text", "")), data={"len": 3})

        registry.register(_spec(), handler)
        env = await registry.execute("demo_echo", {"text": "abc"})
        assert env["ok"] is True
        assert env["content"] == "abc"
        assert env["capability"] == "demo_echo"

    async def test_fail_closed_on_exception(self) -> None:
        registry = CapabilityRegistry()

        async def handler(inputs: dict[str, object]) -> CapabilityOutput:
            raise RuntimeError("boom")

        registry.register(_spec(), handler)
        env = await registry.execute("demo_echo", {})
        assert env["ok"] is False
        assert env["error"]["code"] == "execution_error"
        assert "RuntimeError" in env["error"]["message"]
        assert "Traceback" not in env["error"]["message"]

    async def test_declared_failure_uses_its_code(self) -> None:
        registry = CapabilityRegistry()

        async def handler(inputs: dict[str, object]) -> CapabilityOutput:
            raise CapabilityError("validation_error", "text is required")

        registry.register(_spec(), handler)
        env = await registry.execute("demo_echo", {})
        assert env["error"] == {"code": "validation_error", "message": "text is required"}

    async def test_unknown_capability_is_not_found(self) -> None:
        registry = CapabilityRegistry()
        env = await registry.execute("missing_one", {})
        assert env["ok"] is False
        assert env["error"]["code"] == "not_found"

    async def test_non_dict_input_is_validation_error(self) -> None:
        registry = CapabilityRegistry()

        async def handler(inputs: dict[str, object]) -> CapabilityOutput:
            return CapabilityOutput()

        registry.register(_spec(), handler)
        env = await registry.execute("demo_echo", "not-a-dict")  # type: ignore[arg-type]
        assert env["error"]["code"] == "validation_error"

    def test_duplicate_registration_rejected(self) -> None:
        registry = CapabilityRegistry()

        async def handler(inputs: dict[str, object]) -> CapabilityOutput:
            return CapabilityOutput()

        registry.register(_spec(), handler)
        with pytest.raises(ValueError, match="duplicate capability"):
            registry.register(_spec(), handler)


class TestCapabilityLayerPurity:
    def test_contracts_module_never_reaches_v1_harness(self) -> None:
        """进程内镜像 drift 规则:contracts 公开符号的实现模块不含任何 v1 harness 前缀。"""

        import data_analysis_agent.capabilities.contracts as contracts_mod

        forbidden_prefixes = (
            "data_analysis_agent.agent_loop",
            "data_analysis_agent.session",
            "data_analysis_agent.state_machine",
            "data_analysis_agent.protocol",
            "data_analysis_agent.events",
            "data_analysis_agent.runtime",
            "data_analysis_agent.config",
        )
        for name in dir(contracts_mod):
            module = getattr(getattr(contracts_mod, name), "__module__", "")
            for prefix in forbidden_prefixes:
                assert not module.startswith(prefix), f"contracts.{name} 来自 {module}"
