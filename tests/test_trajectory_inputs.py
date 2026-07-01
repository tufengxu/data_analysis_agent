from data_analysis_agent.config import AgentConfig


def test_enable_trajectory_inputs_defaults_true():
    assert AgentConfig().enable_trajectory_inputs is True
