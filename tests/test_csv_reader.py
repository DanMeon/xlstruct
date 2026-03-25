"""Tests for CsvReader dialect auto-detection."""

import csv
from unittest.mock import patch

from xlstruct.reader.csv_reader import CsvReader

reader = CsvReader()


# * Delimiter auto-detection


def test_semicolon_delimiter() -> None:
    """Semicolon-separated CSV is parsed correctly."""
    content = "name;age;city\nAlice;30;Seoul\nBob;25;Busan\n"
    wb = reader.read(content.encode())

    sheet = wb.sheets[0]
    assert sheet.row_count == 3
    assert sheet.col_count == 3

    values = {(c.row, c.col): c.value for c in sheet.cells}
    assert values[(1, 1)] == "name"
    assert values[(2, 2)] == 30
    assert values[(2, 3)] == "Seoul"
    assert values[(3, 1)] == "Bob"


def test_tab_delimiter() -> None:
    """Tab-separated CSV is parsed correctly."""
    content = "id\tproduct\tprice\n1\tWidget\t9.99\n2\tGadget\t19.50\n"
    wb = reader.read(content.encode())

    sheet = wb.sheets[0]
    assert sheet.row_count == 3
    assert sheet.col_count == 3

    values = {(c.row, c.col): c.value for c in sheet.cells}
    assert values[(1, 2)] == "product"
    assert values[(2, 1)] == 1
    assert values[(2, 3)] == 9.99
    assert values[(3, 2)] == "Gadget"


def test_pipe_delimiter() -> None:
    """Pipe-separated CSV is parsed correctly."""
    content = "a|b|c\n10|20|30\n40|50|60\n"
    wb = reader.read(content.encode())

    sheet = wb.sheets[0]
    assert sheet.row_count == 3
    assert sheet.col_count == 3

    values = {(c.row, c.col): c.value for c in sheet.cells}
    assert values[(1, 1)] == "a"
    assert values[(2, 2)] == 20
    assert values[(3, 3)] == 60


# * Sniffer failure fallback


def test_sniffer_failure_falls_back_to_comma() -> None:
    """When Sniffer raises csv.Error, the reader falls back to comma."""
    content = "x,y,z\n1,2,3\n"

    with patch.object(csv.Sniffer, "sniff", side_effect=csv.Error("cannot determine")):
        wb = reader.read(content.encode())

    sheet = wb.sheets[0]
    values = {(c.row, c.col): c.value for c in sheet.cells}
    assert values[(1, 1)] == "x"
    assert values[(2, 1)] == 1
    assert values[(2, 3)] == 3


# * Type parsing with non-comma delimiters


def test_boolean_parsing_semicolon() -> None:
    """Boolean values are parsed correctly with semicolon delimiter."""
    content = "flag;label\ntrue;yes\nfalse;no\n"
    wb = reader.read(content.encode())

    values = {(c.row, c.col): c.value for c in wb.sheets[0].cells}
    types = {(c.row, c.col): c.data_type for c in wb.sheets[0].cells}

    assert values[(2, 1)] is True
    assert values[(3, 1)] is False
    assert types[(2, 1)] == "b"
    assert types[(3, 1)] == "b"


def test_numeric_parsing_tab() -> None:
    """Integer and float parsing works with tab delimiter."""
    content = "int_col\tfloat_col\n42\t3.14\n-7\t0.001\n"
    wb = reader.read(content.encode())

    values = {(c.row, c.col): c.value for c in wb.sheets[0].cells}
    types = {(c.row, c.col): c.data_type for c in wb.sheets[0].cells}

    assert values[(2, 1)] == 42
    assert values[(2, 2)] == 3.14
    assert values[(3, 1)] == -7
    assert values[(3, 2)] == 0.001
    assert types[(2, 1)] == "n"
    assert types[(2, 2)] == "n"


def test_mixed_types_pipe() -> None:
    """Mixed types (string, int, float, bool) with pipe delimiter."""
    content = "name|count|ratio|active\nAlpha|10|0.5|true\nBeta|20|1.0|false\n"
    wb = reader.read(content.encode())

    values = {(c.row, c.col): c.value for c in wb.sheets[0].cells}
    types = {(c.row, c.col): c.data_type for c in wb.sheets[0].cells}

    # ^ Row 2
    assert values[(2, 1)] == "Alpha"
    assert types[(2, 1)] == "s"
    assert values[(2, 2)] == 10
    assert types[(2, 2)] == "n"
    assert values[(2, 3)] == 0.5
    assert types[(2, 3)] == "n"
    assert values[(2, 4)] is True
    assert types[(2, 4)] == "b"
