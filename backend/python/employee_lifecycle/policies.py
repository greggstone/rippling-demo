"""Business rules for benefits eligibility, payroll setup and access.

All rules are pure functions of the employee record so that the same input
always produces the same downstream configuration.
"""

from typing import Any

from .models import Employee

BASE_PLANS = ["medical_ppo", "dental_basic", "vision_basic"]

STATE_TAX_CODES = {
    "CA": "CA-PIT",
    "NY": "NY-IT-2104",
    "TX": "NONE",
    "WA": "NONE",
}


def benefits_plans(employee: Employee) -> list[str]:
    plans = list(BASE_PLANS)
    if employee.work_country == "US":
        plans.append("401k_match")
        if employee.work_state == "CA":
            plans.append("ca_sdi")
    else:
        plans.append("intl_pension")
    if employee.level >= 5:
        plans.append("executive_life")
    if employee.is_remote:
        plans.append("remote_stipend")
    return sorted(plans)


def policy_assignments(employee: Employee) -> dict[str, str]:
    return {
        "pto": "unlimited" if employee.level >= 4 else "accrual_standard",
        "expense": "manager_tier" if employee.is_manager else "standard_tier",
        "equipment": "remote_kit" if employee.is_remote else "office_kit",
    }


def payroll_configuration(employee: Employee) -> dict[str, Any]:
    if employee.work_country == "US":
        pay_group = "US-SEMIMONTHLY"
        tax_code = STATE_TAX_CODES.get(employee.work_state, f"{employee.work_state}-DEFAULT")
    else:
        pay_group = f"{employee.work_country}-MONTHLY"
        tax_code = f"{employee.work_country}-NATIONAL"
    return {
        "pay_group": pay_group,
        "tax_jurisdiction": tax_code,
        "annual_salary_cents": employee.annual_salary_cents,
        "currency": employee.pay_currency,
        "cost_center": employee.department.upper().replace(" ", "_"),
    }


def required_access_groups(employee: Employee) -> set[str]:
    groups = {"all-employees", f"dept-{employee.department.lower().replace(' ', '-')}"}
    groups.add(f"region-{employee.work_country.lower()}")
    if employee.is_manager:
        groups.add("people-managers")
    if employee.level >= 5:
        groups.add("leadership")
    if employee.department.lower() == "engineering":
        groups.add("source-control")
    if employee.department.lower() in {"finance", "payroll"}:
        groups.add("finance-systems")
    return groups
