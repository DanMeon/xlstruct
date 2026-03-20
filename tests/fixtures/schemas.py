"""Pydantic schemas for test fixtures.

Organized by difficulty tier to progressively validate xlstruct quality.
Each schema includes a recommended ExtractionConfig factory.

Tier 1 — Baseline: flat tables, direct 1:1 column mapping
Tier 2 — Moderate: sheet selection, formula values, instructions needed
Tier 3 — Hard: merged cells, form+table hybrid, structural understanding
Tier 4 — Expert: vertical merge fill-down, 3-level headers, pivot unpivot
"""

from pydantic import BaseModel, Field

from xlstruct.config import ExtractionConfig

# * ──────────────────────────────────────────────────────────────────────
# * Tier 1 — Baseline
# * ──────────────────────────────────────────────────────────────────────


class Employee(BaseModel):
    """simple_employee_directory.xlsx — 30 rows, flat table."""

    employee_id: str = Field(description="e.g. EMP-1001")
    full_name: str
    department: str
    email: str
    hire_date: str = Field(description="ISO format YYYY-MM-DD")
    annual_salary: float
    active: bool


EMPLOYEE_CONFIG = ExtractionConfig(
    output_schema=Employee,
    header_rows=[1],
)


class InventoryItem(BaseModel):
    """legacy_inventory.xls — 20 rows, hardware store inventory."""

    sku: str = Field(description="e.g. SKU-1001")
    product_name: str
    category: str
    qty_in_stock: int
    unit_cost: float
    reorder_level: int
    last_restocked: str = Field(description="YYYY-MM-DD")


INVENTORY_CONFIG = ExtractionConfig(
    output_schema=InventoryItem,
    header_rows=[1],
)


class StudentGrade(BaseModel):
    """legacy_student_grades.xls — 40 rows, pre-computed grades."""

    student_id: str
    name: str
    math: int
    english: int
    science: int
    history: int
    art: int
    average: float
    grade_letter: str = Field(description="A/B/C/D/F")
    pass_fail: str = Field(description="Pass or Fail")


STUDENT_GRADE_CONFIG = ExtractionConfig(
    output_schema=StudentGrade,
    header_rows=[1],
)


# * ──────────────────────────────────────────────────────────────────────
# * Tier 2 — Moderate
# * ──────────────────────────────────────────────────────────────────────


class Transaction(BaseModel):
    """large_transaction_log.xlsx — 200 rows, tests chunking.

    Uses sheet="Transactions" to target the correct sheet.
    """

    txn_id: str = Field(description="e.g. TXN-10001")
    date: str = Field(description="YYYY-MM-DD")
    customer_name: str
    category: str = Field(
        description="Electronics, Peripherals, Furniture, Accessories, or Office Supplies"
    )
    description: str = Field(description="Product name")
    amount: float
    payment_method: str
    status: str = Field(description="Completed, Pending, Refunded, or Failed")


TRANSACTION_CONFIG = ExtractionConfig(
    output_schema=Transaction,
    header_rows=[1],
    sheet="Transactions",
)


class PayrollEntry(BaseModel):
    """legacy_payroll.xls — 50 rows, financial data."""

    employee_id: str
    name: str
    department: str
    base_salary: float
    overtime_hours: float
    overtime_pay: float
    bonus: float
    deductions: float
    net_pay: float


PAYROLL_CONFIG = ExtractionConfig(
    output_schema=PayrollEntry,
    header_rows=[1],
)


class DepartmentBudget(BaseModel):
    """formula_budget_tracker.xlsm — 12 departments.

    Tests extraction of formula-computed values (YTD, Variance, % Used).
    The LLM must return the computed values, not the formula strings.
    """

    department: str
    annual_budget: float
    ytd_actual: float = Field(description="Sum of all monthly actuals (formula result)")
    variance: float = Field(description="Budget minus YTD actual (formula result)")
    percent_used: float = Field(description="Decimal ratio, e.g. 0.95 means 95%")


BUDGET_CONFIG = ExtractionConfig(
    output_schema=DepartmentBudget,
    header_rows=[3],
    instructions=(
        "Extract each department's budget summary. "
        "The YTD Actual, Variance, and % Used columns contain formulas — use the computed values. "
        "Skip the COMPANY TOTAL row."
    ),
)


# * ──────────────────────────────────────────────────────────────────────
# * Tier 3 — Hard
# * ──────────────────────────────────────────────────────────────────────


class FinancialLineItem(BaseModel):
    """merged_financial_report.xlsx — 2-level merged headers.

    The report has section headers (REVENUE, EXPENSES) as merged rows,
    then individual line items under each section.
    Quarterly headers merge across 3 month columns each.
    """

    section: str = Field(description="'Revenue' or 'Expenses'")
    category: str = Field(description="e.g. 'Product Sales', 'Salaries & Benefits'")
    fy_total: float = Field(description="Full-year total (rightmost column, formula result)")


FINANCIAL_CONFIG = ExtractionConfig(
    output_schema=FinancialLineItem,
    header_rows=[3, 4],
    instructions=(
        "This is a financial report with REVENUE and EXPENSES sections. "
        "Extract each line item with its section and FY Total. "
        "Skip section header rows (REVENUE, EXPENSES), subtotal rows (Total Revenue, Total Expenses), "
        "and the NET INCOME row. Only extract individual category rows."
    ),
)


class InvoiceLineItem(BaseModel):
    """complex_invoice_form.xlsm — form header + line items.

    The invoice has a company header, metadata (Invoice #, Date, etc.),
    and a Bill To section above the actual line items table.
    The LLM must identify and extract only the table portion.
    """

    line_number: int
    description: str
    quantity: int
    unit_price: float
    tax_rate: float = Field(description="Decimal, e.g. 0.08 for 8%")
    line_total: float = Field(description="Qty * Unit Price * (1 + Tax Rate), formula result")


INVOICE_CONFIG = ExtractionConfig(
    output_schema=InvoiceLineItem,
    header_rows=[19],
    instructions=(
        "This is an invoice document. The line items table starts at row 19 with headers: "
        "# | Description | Qty | Unit Price | Tax Rate | Line Total. "
        "Extract only the line item rows. Ignore company header, metadata, and summary rows."
    ),
)


class OrderRecord(BaseModel):
    """edge_mixed_layout.xlsx — form header, table, notes.

    Mixed layout: company banner (rows 1-2), metadata (rows 4-8),
    section header (row 10), table (rows 11+), then notes.
    """

    order_id: str = Field(description="e.g. ORD-50000")
    customer: str
    product: str
    quantity: int
    unit_price: float
    total: float = Field(description="Qty * Unit Price, formula result")
    status: str = Field(description="Shipped, Processing, Delivered, or Cancelled")


ORDER_CONFIG = ExtractionConfig(
    output_schema=OrderRecord,
    header_rows=[11],
    instructions=(
        "Extract order records from the ORDER DETAILS table. "
        "The table starts at row 11. Ignore the report header, metadata, and notes sections."
    ),
)


# * ──────────────────────────────────────────────────────────────────────
# * Tier 4 — Expert
# * ──────────────────────────────────────────────────────────────────────


class RegionalProductSales(BaseModel):
    """multi_level_header_sales.xlsm — 3-level merged headers.

    Headers: Region (level 1) → Revenue/Units/Margin% (level 2).
    Category separator rows group products.
    The LLM must flatten the multi-level header into one record per product-region pair.
    """

    category: str = Field(description="Product category from separator rows, e.g. 'Electronics'")
    product: str
    region: str = Field(description="North America, Europe, Asia Pacific, or Latin America")
    revenue: float
    units: int
    margin_pct: float = Field(description="Decimal, e.g. 0.25 for 25%")


REGIONAL_SALES_CONFIG = ExtractionConfig(
    output_schema=RegionalProductSales,
    header_rows=[3, 4],
    instructions=(
        "This report has 3-level merged headers: Product column, then Region groups "
        "(North America, Europe, Asia Pacific, Latin America), each with Revenue/Units/Margin% sub-columns. "
        "Products are grouped by category (italic separator rows like 'Electronics', 'Peripherals'). "
        "Create one record per product-region combination. "
        "Inherit the category from the nearest category separator row above each product."
    ),
)


class OrgMember(BaseModel):
    """edge_nested_groups.xlsx — 3-level vertical merge fill-down.

    Department, Team, and Sub-Team columns use vertical merged cells.
    Each merged cell spans multiple employee rows.
    The LLM must 'fill down' the merged values to each row.
    """

    department: str = Field(
        description="From vertically merged cells: Engineering, Sales, or Marketing"
    )
    team: str = Field(description="From vertically merged cells, e.g. Platform, Product, Data")
    sub_team: str = Field(
        description="From vertically merged cells, e.g. Backend, Frontend, ML Engineering"
    )
    employee_id: str
    name: str
    title: str
    location: str
    start_date: str = Field(description="YYYY-MM-DD")


ORG_MEMBER_CONFIG = ExtractionConfig(
    output_schema=OrgMember,
    header_rows=[3],
    instructions=(
        "This is an organization directory with hierarchical grouping. "
        "The Department, Team, and Sub-Team columns use vertically merged cells — "
        "each merged cell applies to all employee rows within that group. "
        "Fill down the department/team/sub-team values for every employee row. "
        "Skip the title row and the headcount summary at the bottom."
    ),
)


class ProductMonthlySales(BaseModel):
    """edge_sparse_pivot.xlsx — pivot-style cross-tab, sparse data.

    Row headers: Category (vertical merge) + Product.
    Column headers: Quarter (merged) → Month.
    Many cells are empty (product not sold that month).
    The LLM must unpivot into one record per product-month with a sale.
    """

    category: str = Field(
        description="From vertically merged cells: Hardware, Software, Networking, or Services"
    )
    product: str
    month: str = Field(description="3-letter abbreviation: Jan, Feb, ..., Dec")
    units_sold: int = Field(
        description="Number of units sold; only include months with actual data"
    )


PIVOT_SALES_CONFIG = ExtractionConfig(
    output_schema=ProductMonthlySales,
    header_rows=[3, 4],
    instructions=(
        "This is a pivot-style table: products (rows) × months (columns). "
        "Categories are in vertically merged cells in column A. "
        "Months are grouped under quarterly headers (Q1-Q4). "
        "Unpivot the data: create one record per product-month combination "
        "where units_sold is not empty. Skip empty cells (no sale that month). "
        "Fill down the category from merged cells."
    ),
)


# * ──────────────────────────────────────────────────────────────────────
# * Quick-reference map
# * ──────────────────────────────────────────────────────────────────────

FIXTURE_CONFIGS: dict[str, ExtractionConfig] = {
    "simple_employee_directory.xlsx": EMPLOYEE_CONFIG,
    "legacy_inventory.xls": INVENTORY_CONFIG,
    "legacy_student_grades.xls": STUDENT_GRADE_CONFIG,
    "large_transaction_log.xlsx": TRANSACTION_CONFIG,
    "legacy_payroll.xls": PAYROLL_CONFIG,
    "formula_budget_tracker.xlsm": BUDGET_CONFIG,
    "merged_financial_report.xlsx": FINANCIAL_CONFIG,
    "complex_invoice_form.xlsm": INVOICE_CONFIG,
    "edge_mixed_layout.xlsx": ORDER_CONFIG,
    "multi_level_header_sales.xlsm": REGIONAL_SALES_CONFIG,
    "edge_nested_groups.xlsx": ORG_MEMBER_CONFIG,
    "edge_sparse_pivot.xlsx": PIVOT_SALES_CONFIG,
}
