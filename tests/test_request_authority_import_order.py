from __future__ import annotations

import sys


def test_runtime_request_parameter_installer_does_not_require_compiler_modules() -> None:
    from ai_test_asset_center import request_build_contract as request
    from ai_test_asset_center.validation_parameter_authority import (
        install_request_parameter_contract_authority,
    )

    install_request_parameter_contract_authority()
    assert request._query_contract.__name__ == (
        "query_contract_with_declared_required_removal"
    )

    # The runtime-only installer itself has no need to import these modules.
    # They may already be present because another test imported the full product,
    # so validate the installer module's dependency surface rather than asserting
    # global process absence.
    import ai_test_asset_center.validation_parameter_authority as authority

    names = set(authority.install_request_parameter_contract_authority.__code__.co_names)
    assert "_validation_obligation_expander_core" not in names
    assert "experiment_protocols_privacy_base" not in names


def test_compile_freezer_binds_current_governed_request_builder() -> None:
    import ai_test_asset_center.request_build_contract as request
    import ai_test_asset_center.experiment_compile_freezer as freezer

    assert freezer.build_request_build_contract is request.build_request_build_contract
    assert freezer.build_request_build_contract.__name__ == (
        "governed_build_request_build_contract"
    )


def test_request_authority_installers_are_idempotent() -> None:
    from ai_test_asset_center import request_build_contract as request
    from ai_test_asset_center.request_header_transport_authority import (
        install_request_header_transport_authority,
    )
    from ai_test_asset_center.validation_parameter_authority import (
        install_request_parameter_contract_authority,
    )

    install_request_parameter_contract_authority()
    install_request_header_transport_authority()
    first_builder = request.build_request_build_contract
    first_query = request._query_contract

    install_request_parameter_contract_authority()
    install_request_header_transport_authority()
    assert request.build_request_build_contract is first_builder
    assert request._query_contract is first_query
