import argparse
import os
import sys

import pandas as pd

from transform_validator.config import AppConfig, MappingConfig
from transform_validator.dictionary_loader import load_dictionary
from transform_validator.mapping import map_source_to_normalized
from transform_validator.validation import (
    aggregate_normalized,
    compare_with_output,
)
from transform_validator.report import write_report_excel
from transform_validator.normalizer import Normalizer

# Listas de nombres candidatos (en orden de prioridad)
CANDIDATE_NAMEPLATE = ["familia", "modelo"]
CANDIDATE_TRIM = [
    "version simple",
    "versión",
    "modelo / versión",
    "vigencia",
    "vigente?",
    "vigente",
]
CANDIDATE_PARAM = ["ratio", "factor"]

def infer_country_from_output_path(output_path: str, output_df: pd.DataFrame | None) -> str:
    base_name = os.path.basename(output_path)
    name_upper = base_name.upper()

    if name_upper.startswith("OUTPUT_") and "." in name_upper:
        country_part = name_upper.split("_", 1)[1].rsplit(".", 1)[0].strip()
        if country_part:
            return country_part

    if output_df is not None and "COUNTRY" in output_df.columns and not output_df["COUNTRY"].dropna().empty:
        return str(output_df["COUNTRY"].iloc[0]).strip().upper()

    raise ValueError(
        "No se pudo inferir el país a partir del nombre del archivo de salida "
        "ni de la columna COUNTRY."
    )

def detect_header_row(path: str, sheet: str, candidate_headers: list[str]) -> int:
    norm = Normalizer()
    tmp = pd.read_excel(path, sheet_name=sheet, header=None, engine="openpyxl")

    target_norms = {norm.standardize_text(h) for h in candidate_headers}

    best_idx = None
    best_score = -1
    best_non_null = -1

    for i, row in tmp.iterrows():
        values = [v for v in row.values if pd.notna(v)]
        if not values:
            continue

        values_norm = [norm.standardize_text(v) for v in values]
        hits = sum(1 for v in values_norm if v in target_norms)
        non_null = len(values)

        if hits > best_score or (hits == best_score and non_null > best_non_null):
            best_score = hits
            best_non_null = non_null
            best_idx = i

    if best_idx is None or best_score <= 0:
        raise ValueError(
            "No se encontró una fila de encabezados que contenga alguna de las columnas "
            f"candidatas: {candidate_headers} en la hoja '{sheet}'. "
            "Revisa el archivo o usa --header-row."
        )

    return best_idx

def auto_select_column(columns, candidates: list[str]) -> str | None:
    norm = Normalizer()
    norm_cols = {col: norm.standardize_text(col) for col in columns}

    for cand in candidates:
        cand_norm = norm.standardize_text(cand)
        for col, col_norm in norm_cols.items():
            if col_norm == cand_norm:
                return col

    return None

def parse_args() -> AppConfig:
    parser = argparse.ArgumentParser(
        description=(
            "Validador de transformaciones de inventario.\n\n"
            "Ejemplo:\n"
            "  python app.py --dict Base.xlsx "
            "--original \"Lector Inventory 11+1 Closing 2025-2026.xlsx\" BASE "
            "--output OUTPUT_CHILE.xlsx"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument(
        "--dict",
        dest="dict_path",
        required=True,
        help="Ruta al diccionario (Base.xlsx).",
    )
    parser.add_argument(
        "--original",
        dest="original_path",
        required=True,
        help="Ruta al archivo con la base original.",
    )
    parser.add_argument(
        "sheet_name",
        help="Nombre de la hoja de Excel con los datos originales (por ejemplo, BASE, Data, MV-Frcst).",
    )
    parser.add_argument(
        "--output",
        dest="output_path",
        required=True,
        help="Ruta al archivo normalizado OUTPUT_<PAIS>.xlsx.",
    )
    parser.add_argument(
        "--header-row",
        dest="header_row",
        default="auto",
        help="Fila (0-based) donde están los encabezados de la base original. Usa 'auto' para detección automática.",
    )
    parser.add_argument(
        "--nameplate-col",
        dest="nameplate_col",
        default=None,
        help=(
            "Nombre de la columna que representa la familia / nameplate en la base original. "
            "Si no se indica, se detecta automáticamente usando: "
            f"{CANDIDATE_NAMEPLATE}"
        ),
    )
    parser.add_argument(
        "--trim-col",
        dest="trim_col",
        default=None,
        help=(
            "Nombre de la columna que representa el trim en la base original. "
            "Si no se indica, se detecta automáticamente usando: "
            f"{CANDIDATE_TRIM}"
        ),
    )
    parser.add_argument(
        "--param-col",
        dest="param_col",
        default=None,
        help=(
            "Nombre de la columna que representa el parámetro (Production, Embarques, etc.) en la base original. "
            "Si no se indica, se detecta automáticamente usando: "
            f"{CANDIDATE_PARAM}"
        ),
    )

    args = parser.parse_args()

    header_row = None
    if isinstance(args.header_row, str) and args.header_row.lower() != "auto":
        header_row = int(args.header_row)

    return AppConfig(
        dict_path=args.dict_path,
        original_path=args.original_path,
        original_sheet=args.sheet_name,
        output_path=args.output_path,
        country="",
        header_row=header_row,
        nameplate_column=args.nameplate_col,
        trim_column=args.trim_col,
        parameter_column=args.param_col,
    )

def main() -> int:
    try:
        config = parse_args()

        print("Leyendo archivo normalizado de salida...")
        output_df = pd.read_excel(config.output_path, engine="openpyxl")

        country = infer_country_from_output_path(config.output_path, output_df)
        config.country = country
        print(f"País inferido: {country}")

        print("Cargando diccionario...")
        dict_dfs = load_dictionary(config.dict_path)

        if country.upper() not in dict_dfs:
            raise ValueError(
                f"No se encontró una hoja '{country}' en el diccionario {config.dict_path}."
            )

        print(f"Leyendo base original (hoja '{config.original_sheet}')...")
        if config.header_row is None:
            candidate_headers = CANDIDATE_NAMEPLATE + CANDIDATE_TRIM
            header_row = detect_header_row(
                config.original_path,
                config.original_sheet,
                candidate_headers,
            )
            print(f"Fila de encabezado detectada automáticamente: {header_row}")
        else:
            header_row = config.header_row
            print(f"Usando fila de encabezado indicada: {header_row}")

        source_df = pd.read_excel(
            config.original_path,
            sheet_name=config.original_sheet,
            header=header_row,
            engine="openpyxl",
        )

        nameplate_col = config.nameplate_column
        trim_col = config.trim_column
        param_col = config.parameter_column

        if not nameplate_col:
            nameplate_col = auto_select_column(source_df.columns, CANDIDATE_NAMEPLATE)
            if not nameplate_col:
                raise ValueError(
                    "No se pudo detectar automáticamente la columna de familia/nameplate. "
                    f"Columnas disponibles: {list(source_df.columns)}"
                )
            print(f"Columna NAMEPLATE detectada: {nameplate_col}")
        else:
            print(f"Columna NAMEPLATE indicada por usuario: {nameplate_col}")

        if not trim_col:
            trim_col = auto_select_column(source_df.columns, CANDIDATE_TRIM)
            if not trim_col:
                raise ValueError(
                    "No se pudo detectar automáticamente la columna de TRIM. "
                    f"Columnas disponibles: {list(source_df.columns)}"
                )
            print(f"Columna TRIM detectada: {trim_col}")
        else:
            print(f"Columna TRIM indicada por usuario: {trim_col}")

        if not param_col:
            param_col = auto_select_column(source_df.columns, CANDIDATE_PARAM)
            if not param_col:
                raise ValueError(
                    "No se pudo detectar automáticamente la columna de parámetro. "
                    f"Columnas disponibles: {list(source_df.columns)}"
                )
            print(f"Columna PARAMETER detectada: {param_col}")
        else:
            print(f"Columna PARAMETER indicada por usuario: {param_col}")

        mapping_cfg = MappingConfig(
            nameplate_column=nameplate_col,
            trim_column=trim_col,
            parameter_column=param_col,
        )

        print("Mapeando filas de la base original al formato normalizado...")
        mapped_df, unmapped_rows = map_source_to_normalized(
            source_df=source_df,
            country=country,
            mapping=mapping_cfg,
            dictionary_dfs=dict_dfs,
        )

        if mapped_df.empty:
            print(
                "Advertencia: no se generó ninguna fila mapeada. "
                "Verifica que el país y las columnas de mapeo sean correctos."
            )

        print("Agregando filas mapeadas para obtener el resultado esperado...")
        expected_df = aggregate_normalized(mapped_df)

        print("Comparando resultado esperado contra archivo de salida...")
        comparison = compare_with_output(
            expected_df=expected_df,
            output_df=output_df,
        )

        comparison["unmapped_df"] = unmapped_rows

        report_path = os.path.splitext(config.output_path)[0] + "_VALIDATION.xlsx"
        print(f"Escribiendo reporte de validación en: {report_path}")
        write_report_excel(
            report_path=report_path,
            summary=comparison["summary"],
            unmapped_rows_df=comparison["unmapped_df"],
            differences_df=comparison["differences_df"],
            diff_matrix_df=comparison["diff_matrix_df"],
        )

        print("Validación completada.")
        return 0

    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())