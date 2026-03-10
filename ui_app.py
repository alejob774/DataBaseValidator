import os
import tempfile
from typing import Optional

import pandas as pd

# Intentar usar TkinterDnD para drag&drop; si no está, usar Tk normal
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
    from tkinter import filedialog, messagebox, StringVar, ttk, Frame, Entry, Button, Label, Tk  # type: ignore
    USE_DND = True
except ImportError:
    from tkinter import Tk, filedialog, messagebox, StringVar, ttk, Frame, Entry, Button, Label  # type: ignore
    DND_FILES = None
    TkinterDnD = None
    USE_DND = False

# Importa tu lógica existente del validador
from transform_validator.config import MappingConfig
from transform_validator.dictionary_loader import load_dictionary
from transform_validator.mapping import map_source_to_normalized
from transform_validator.validation import aggregate_normalized, compare_with_output
from transform_validator.report import write_report_excel
from transform_validator.normalizer import Normalizer

# Mismas listas de candidatos que en app.py
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

def infer_country_from_output_path(output_name: str, output_df: Optional[pd.DataFrame]) -> str:
    base_name = os.path.basename(output_name)
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
    """
    Detecta la fila de cabecera buscando la que mejor coincida con los nombres
    candidatos (familia/modelo, versión simple, modelo / versión, vigente?, ...).
    """
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
            "Revisa el archivo o indica manualmente la fila de cabecera."
        )

    return best_idx

def auto_select_column(columns, candidates: list[str]) -> Optional[str]:
    """
    Selecciona automáticamente la columna de `columns` que coincida con alguno de los
    nombres en `candidates`, respetando el orden de `candidates`.
    """
    norm = Normalizer()
    norm_cols = {col: norm.standardize_text(col) for col in columns}

    for cand in candidates:
        cand_norm = norm.standardize_text(cand)
        for col, col_norm in norm_cols.items():
            if col_norm == cand_norm:
                return col

    return None

def run_validation_pipeline(
    dict_path: str,
    original_path: str,
    sheet_name: str,
    output_path: str,
    header_row: Optional[int] = None,
    nameplate_col: Optional[str] = None,
    trim_col: Optional[str] = None,
    param_col: Optional[str] = None,
) -> str:
    """
    Ejecuta el pipeline de validación y devuelve la ruta al archivo *_VALIDATION.xlsx.
    """
    # 1) Leer archivo de salida
    output_df = pd.read_excel(output_path, engine="openpyxl")

    # 2) Inferir país
    country = infer_country_from_output_path(output_path, output_df)

    # 3) Cargar diccionario
    dict_dfs = load_dictionary(dict_path)

    if country.upper() not in dict_dfs:
        raise ValueError(
            f"No se encontró una hoja '{country}' en el diccionario {dict_path}."
        )

    # 4) Leer base original con detección/forzado de fila de cabecera
    if header_row is None:
        candidate_headers = CANDIDATE_NAMEPLATE + CANDIDATE_TRIM
        header_row = detect_header_row(
            original_path,
            sheet_name,
            candidate_headers,
        )

    source_df = pd.read_excel(
        original_path,
        sheet_name=sheet_name,
        header=header_row,
        engine="openpyxl",
    )

    # 5) Auto-detección de columnas si no se indicaron
    if not nameplate_col:
        nameplate_col = auto_select_column(source_df.columns, CANDIDATE_NAMEPLATE)
        if not nameplate_col:
            raise ValueError(
                "No se pudo detectar automáticamente la columna de familia/nameplate. "
                f"Columnas disponibles: {list(source_df.columns)}"
            )

    if not trim_col:
        trim_col = auto_select_column(source_df.columns, CANDIDATE_TRIM)
        if not trim_col:
            raise ValueError(
                "No se pudo detectar automáticamente la columna de TRIM. "
                f"Columnas disponibles: {list(source_df.columns)}"
            )

    if not param_col:
        param_col = auto_select_column(source_df.columns, CANDIDATE_PARAM)
        if not param_col:
            raise ValueError(
                "No se pudo detectar automáticamente la columna de parámetro. "
                f"Columnas disponibles: {list(source_df.columns)}"
            )

    mapping_cfg = MappingConfig(
        nameplate_column=nameplate_col,
        trim_column=trim_col,
        parameter_column=param_col,
    )

    # 6) Mapeo fila a fila
    mapped_df, unmapped_rows = map_source_to_normalized(
        source_df=source_df,
        country=country,
        mapping=mapping_cfg,
        dictionary_dfs=dict_dfs,
    )

    # 7) Agregación
    expected_df = aggregate_normalized(mapped_df)

    # 8) Comparación con OUTPUT
    comparison = compare_with_output(
        expected_df=expected_df,
        output_df=output_df,
    )
    comparison["unmapped_df"] = unmapped_rows

    # 9) Reporte
    report_path = os.path.splitext(output_path)[0] + "_VALIDATION.xlsx"
    write_report_excel(
        report_path=report_path,
        summary=comparison["summary"],
        unmapped_rows_df=comparison["unmapped_df"],
        differences_df=comparison["differences_df"],
    )

    return report_path

class FileInput(Frame):
    """
    Componente reutilizable: Entry de ruta + Botón 'Buscar...' + (opc) Drag&Drop.
    """

    def __init__(self, master, label_text: str, *args, **kwargs):
        super().__init__(master, *args, **kwargs)

        self.var = StringVar()
        self.label = Label(self, text=label_text)
        self.entry = Entry(self, textvariable=self.var, width=70)
        self.button = Button(self, text="Buscar...", command=self.browse_file)

        self.label.grid(row=0, column=0, sticky="w", padx=(0, 5))
        self.entry.grid(row=0, column=1, sticky="we", padx=(0, 5))
        self.button.grid(row=0, column=2, sticky="e")

        self.columnconfigure(1, weight=1)

        # Drag & Drop si está disponible
        if USE_DND and DND_FILES is not None and hasattr(self.entry, "drop_target_register"):
            self.entry.drop_target_register(DND_FILES)
            self.entry.dnd_bind("<<Drop>>", self._on_drop)

    def _on_drop(self, event):
        data = event.data
        if data.startswith("{") and data.endswith("}"):
            data = data[1:-1]
        path = data.split()[-1]
        self.var.set(path)

    def browse_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("Excel files", "*.xlsx *.xls")]
        )
        if path:
            self.var.set(path)

    def get(self) -> str:
        return self.var.get().strip()

def main():
    # Crear ventana raíz
    if USE_DND and TkinterDnD is not None:
        root = TkinterDnD.Tk()
    else:
        root = Tk()

    root.title("Validador de Normalización")
    root.geometry("900x320")

    # Marco principal
    main_frame = Frame(root)
    main_frame.pack(fill="both", expand=True, padx=10, pady=10)

    # Entradas de archivos
    dict_input = FileInput(main_frame, "Diccionario (Base.xlsx):")
    dict_input.grid(row=0, column=0, sticky="we", pady=5)

    orig_input = FileInput(main_frame, "Base original:")
    orig_input.grid(row=1, column=0, sticky="we", pady=5)

    out_input = FileInput(main_frame, "Output (OUTPUT_<PAIS>.xlsx):")
    out_input.grid(row=2, column=0, sticky="we", pady=5)

    # Dropdown de hojas
    sheet_label = Label(main_frame, text="Hoja de la base original:")
    sheet_label.grid(row=3, column=0, sticky="w", pady=(15, 2))

    sheet_var = StringVar()
    sheet_combo = ttk.Combobox(main_frame, textvariable=sheet_var, state="readonly", width=50)
    sheet_combo.grid(row=4, column=0, sticky="w", pady=2)

    # Fila de cabecera
    header_label = Label(main_frame, text="Fila de cabecera (opcional, 0-based, vacío = auto):")
    header_label.grid(row=5, column=0, sticky="w", pady=(15, 2))

    header_entry = Entry(main_frame, width=10)
    header_entry.grid(row=6, column=0, sticky="w")

    # Actualizar lista de hojas cuando cambia la base original
    def update_sheets_from_original(*_args):
        path = orig_input.get()
        if not path or not os.path.exists(path):
            return
        try:
            xls = pd.ExcelFile(path, engine="openpyxl")
            sheet_combo["values"] = xls.sheet_names
            if xls.sheet_names:
                sheet_combo.current(0)
        except Exception as e:
            messagebox.showerror("Error leyendo hojas", f"No se pudieron leer las hojas del archivo original:\n{e}")

    orig_input.entry.bind("<FocusOut>", update_sheets_from_original)
    orig_input.entry.bind("<Return>", update_sheets_from_original)
    orig_input.button.configure(
        command=lambda: (orig_input.browse_file(), update_sheets_from_original())
    )

    def on_process():
        dict_path = dict_input.get()
        orig_path = orig_input.get()
        out_path = out_input.get()
        sheet = sheet_var.get().strip()
        header_text = header_entry.get().strip()

        if not dict_path or not os.path.exists(dict_path):
            messagebox.showerror("Error", "Debes seleccionar un diccionario válido (Base.xlsx).")
            return
        if not orig_path or not os.path.exists(orig_path):
            messagebox.showerror("Error", "Debes seleccionar una base original válida.")
            return
        if not out_path or not os.path.exists(out_path):
            messagebox.showerror("Error", "Debes seleccionar un archivo OUTPUT_<PAIS>.xlsx válido.")
            return
        if not sheet:
            messagebox.showerror("Error", "Debes seleccionar una hoja de la base original.")
            return

        if header_text == "":
            header_row = None
        else:
            try:
                header_row = int(header_text)
            except ValueError:
                messagebox.showerror(
                    "Error",
                    "La fila de cabecera debe ser un número entero (0-based) o dejarse vacía.",
                )
                return

        try:
            messagebox.showinfo("Procesando", "La validación ha comenzado, por favor espera...")
            report_path = run_validation_pipeline(
                dict_path=dict_path,
                original_path=orig_path,
                sheet_name=sheet,
                output_path=out_path,
                header_row=header_row,
            )
            messagebox.showinfo(
                "Validación completada",
                f"Se generó el archivo de validación:\n{report_path}",
            )
        except Exception as e:
            messagebox.showerror(
                "Error durante la validación",
                f"Ocurrió un error:\n{e}",
            )

    process_button = Button(main_frame, text="Procesar", command=on_process, width=15)
    process_button.grid(row=7, column=0, sticky="w", pady=(20, 0))

    root.mainloop()

if __name__ == "__main__":
    main()