from __future__ import annotations

from typing import Dict, List

import pandas as pd

def aggregate_normalized(mapped_df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrupa las filas mapeadas por:
      COUNTRY, CHANNEL, NAMEPLATE, TRIM, CONCAT, SEGMENT, PARAMETER
    y suma todas las columnas de fecha.
    """
    if mapped_df.empty:
        return mapped_df.copy()

    fixed_cols = [
        "COUNTRY",
        "CHANNEL",
        "NAMEPLATE",
        "TRIM",
        "CONCAT",
        "SEGMENT",
        "PARAMETER",
    ]

    date_cols = [
        c
        for c in mapped_df.columns
        if c not in fixed_cols and c != "_SRC_INDEX"
    ]

    for c in date_cols:
        mapped_df[c] = pd.to_numeric(mapped_df[c], errors="coerce").fillna(0.0)

    grouped = (
        mapped_df
        .drop(columns=["_SRC_INDEX"])
        .groupby(fixed_cols, as_index=False)[date_cols]
        .sum()
    )

    return grouped[fixed_cols + sorted(date_cols)]

def compare_with_output(
    expected_df: pd.DataFrame,
    output_df: pd.DataFrame,
    tolerance: float = 1e-6,
) -> Dict[str, pd.DataFrame | Dict[str, float]]:
    """
    Compara el DataFrame esperado (resultado de la agregación del mapeo)
    contra el archivo normalizado de salida.

    - Igualamos por:
        COUNTRY, CHANNEL, NAMEPLATE, TRIM, CONCAT, SEGMENT, PARAMETER
    - Calculamos:
        * differences_df: detalle por celda con diferencia (incluye FILA del OUTPUT).
        * diff_matrix_df: matriz con DIFF por celda (0 = correcto) para heatmap.
    """
    key_cols = [
        "COUNTRY",
        "CHANNEL",
        "NAMEPLATE",
        "TRIM",
        "CONCAT",
        "SEGMENT",
        "PARAMETER",
    ]

    # Columnas de fecha (todas las que no son clave)
    date_cols = [
        c
        for c in expected_df.columns
        if c not in key_cols
    ]

    exp_sub = expected_df[key_cols + date_cols].copy()

    missing_keys = [c for c in key_cols if c not in output_df.columns]
    missing_dates = [c for c in date_cols if c not in output_df.columns]

    if missing_keys:
        raise ValueError(
            f"El archivo de salida no contiene las columnas clave requeridas: {missing_keys}"
        )
    if missing_dates:
        raise ValueError(
            f"El archivo de salida no contiene todas las columnas de fecha requeridas: {missing_dates}"
        )

    out_sub = output_df[key_cols + date_cols].copy()

    # Asegurar numérico en fechas
    for c in date_cols:
        exp_sub[c] = pd.to_numeric(exp_sub[c], errors="coerce").fillna(0.0)
        out_sub[c] = pd.to_numeric(out_sub[c], errors="coerce").fillna(0.0)

    # Guardar índice de salida para poder reportar FILA
    out_sub = out_sub.reset_index().rename(columns={"index": "OUT_ROW"})

    merged = exp_sub.merge(
        out_sub,
        on=key_cols,
        how="outer",
        suffixes=("_EXP", "_OUT"),
        indicator=True,
    )

    left_only = merged[merged["_merge"] == "left_only"].copy()
    right_only = merged[merged["_merge"] == "right_only"].copy()
    both = merged[merged["_merge"] == "both"].copy()

    # ------------------------------
    # 1) Detalle de diferencias por celda (differences_df)
    # ------------------------------
    diff_rows: List[Dict[str, object]] = []

    for _, row in both.iterrows():
        out_row = row.get("OUT_ROW")
        fila_excel = int(out_row) + 2 if pd.notna(out_row) else None

        for col in date_cols:
            exp_val = row[f"{col}_EXP"]
            out_val = row[f"{col}_OUT"]

            if pd.isna(exp_val) and pd.isna(out_val):
                continue

            diff = float(exp_val) - float(out_val)
            if abs(diff) > tolerance:
                record = {k: row[k] for k in key_cols}
                record["COLUMN"] = col
                record["EXPECTED"] = float(exp_val)
                record["OUTPUT"] = float(out_val)
                record["DIFF"] = diff
                record["FILA"] = fila_excel
                diff_rows.append(record)

    differences_df = pd.DataFrame(diff_rows)

    # ------------------------------
    # 2) Matriz de diferencias para heatmap (diff_matrix_df)
    #    DIFF = EXPECTED - OUTPUT
    #    0 => correcto, !=0 => error
    # ------------------------------
    # both: expected y output presentes
    matrix_both = both[key_cols + ["OUT_ROW"]].copy()
    for col in date_cols:
        exp_vals = pd.to_numeric(both[f"{col}_EXP"], errors="coerce").fillna(0.0)
        out_vals = pd.to_numeric(both[f"{col}_OUT"], errors="coerce").fillna(0.0)
        matrix_both[col] = exp_vals - out_vals

    # left_only: falta en output => OUTPUT = 0
    matrix_left = left_only[key_cols].copy()
    matrix_left["OUT_ROW"] = pd.NA
    for col in date_cols:
        exp_vals = pd.to_numeric(left_only[f"{col}_EXP"], errors="coerce").fillna(0.0)
        matrix_left[col] = exp_vals  # diff = exp - 0

    # right_only: extra en output => EXPECTED = 0
    matrix_right = right_only[key_cols + ["OUT_ROW"]].copy()
    for col in date_cols:
        out_vals = pd.to_numeric(right_only[f"{col}_OUT"], errors="coerce").fillna(0.0)
        matrix_right[col] = -out_vals  # diff = 0 - out

    diff_matrix_df = pd.concat(
        [matrix_both, matrix_left, matrix_right],
        ignore_index=True,
    )

    # Añadir columna FILA (fila de Excel del OUTPUT)
    def _fila_from_out_row(v):
        if pd.isna(v):
            return pd.NA
        try:
            return int(v) + 2  # fila 2 = primera fila de datos (encabezado en 1)
        except Exception:
            return pd.NA

    diff_matrix_df["FILA"] = diff_matrix_df["OUT_ROW"].apply(_fila_from_out_row)

    # Ordenar columnas: claves + FILA + fechas
    diff_matrix_df = diff_matrix_df[
        key_cols + ["FILA"] + date_cols
    ].copy()

    # ------------------------------
    # 3) Resumen
    # ------------------------------
    summary = {
        "total_expected_rows": float(len(exp_sub)),
        "total_output_rows": float(len(out_sub)),
        "missing_in_output_rows": float(len(left_only)),
        "extra_in_output_rows": float(len(right_only)),
        "differences_cells": float(len(differences_df)),
    }

    return {
        "summary": summary,
        "unmapped_df": pd.DataFrame(),  # se rellena en app.py
        "differences_df": differences_df,
        "diff_matrix_df": diff_matrix_df,
        "merged_full": merged,
    }