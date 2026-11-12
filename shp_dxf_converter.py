# -*- coding: utf-8 -*-
"""
SHP -> DXF Converter
Windows 11 GUI utility. Converts all records in a Shapefile to AutoCAD 2007 DXF.
Geometry is written as CAD entities; selected attribute fields are written as TEXT.
"""
import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import shapefile
import ezdxf
from pyproj import CRS, Transformer
from shapely.geometry import shape
from shapely.ops import transform as shapely_transform


APP_TITLE = "SHP → DXF Converter"
DXF_VERSION = "R2007"

# AutoCAD ACI colors. Entity colors are inherited from their layers (BYLAYER).
ACI_COLORS = {
    "흰색(7)": 7, "빨강(1)": 1, "노랑(2)": 2, "초록(3)": 3,
    "청록(4)": 4, "파랑(5)": 5, "자홍(6)": 6,
    "회색(8)": 8, "연회색(9)": 9,
}
FONT_OPTIONS = {
    "맑은 고딕": "malgun.ttf",
    "굴림": "gulim.ttc",
    "돋움": "dotum.ttc",
    "Arial": "arial.ttf",
    "Romans (SHX)": "romans.shx",
    "Simplex (SHX)": "simplex.shx",
}


def safe_layer(value):
    value = str(value or "SHP_LAYER").strip()
    value = value.replace(" ", "_")
    for ch in '<>/\\":;?*|=,':
        value = value.replace(ch, "_")
    return value[:200] or "SHP_LAYER"


def field_text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("cp949", errors="replace")
    return str(value).strip()


def iter_lines(geom):
    """Yield coordinate sequences from line and polygon geometries."""
    gt = geom.geom_type
    if gt == "LineString":
        yield list(geom.coords), False
    elif gt == "LinearRing":
        yield list(geom.coords), True
    elif gt == "MultiLineString":
        for g in geom.geoms:
            yield list(g.coords), False
    elif gt == "Polygon":
        yield list(geom.exterior.coords), True
        for ring in geom.interiors:
            yield list(ring.coords), True
    elif gt == "MultiPolygon":
        for poly in geom.geoms:
            yield list(poly.exterior.coords), True
            for ring in poly.interiors:
                yield list(ring.coords), True
    elif gt == "GeometryCollection":
        for g in geom.geoms:
            yield from iter_lines(g)


def add_geometry(msp, geom, layer):
    if geom.is_empty:
        return 0
    count = 0
    gt = geom.geom_type
    if gt == "Point":
        msp.add_point((geom.x, geom.y), dxfattribs={"layer": layer})
        count += 1
    elif gt == "MultiPoint":
        for p in geom.geoms:
            msp.add_point((p.x, p.y), dxfattribs={"layer": layer})
            count += 1
    else:
        for coords, closed in iter_lines(geom):
            if len(coords) >= 2:
                # DXF LWPOLYLINE uses XY; Z values are intentionally omitted for 2D cadastral data.
                xy = [(c[0], c[1]) for c in coords]
                if closed and len(xy) > 2 and xy[0] == xy[-1]:
                    xy = xy[:-1]
                msp.add_lwpolyline(
                    xy, close=closed, dxfattribs={"layer": layer}
                )
                count += 1
    return count


def label_anchor(geom):
    # Representative point is guaranteed to lie inside polygons.
    p = geom.representative_point()
    return p.x, p.y


def convert(shp_path, out_path, target_epsg, label_fields, encoding, progress_cb, geom_color, text_color, font_file, text_height):
    reader = shapefile.Reader(shp_path, encoding=encoding)
    field_names = [f[0] for f in reader.fields[1:]]
    missing = [f for f in label_fields if f not in field_names]
    if missing:
        raise ValueError("선택한 속성 필드를 찾을 수 없습니다: " + ", ".join(missing))

    prj_path = os.path.splitext(shp_path)[0] + ".prj"
    if not os.path.exists(prj_path):
        raise ValueError(
            "같은 폴더에 .prj 파일이 없습니다.\n"
            "원본 좌표계를 확인할 수 없어 좌표 변환을 중단했습니다.\n"
            "SHP의 좌표계를 확인한 뒤 .prj를 준비해 주세요."
        )
    with open(prj_path, "r", encoding="utf-8", errors="ignore") as f:
        source_crs = CRS.from_wkt(f.read())

    target_crs = CRS.from_user_input(target_epsg)
    transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)

    doc = ezdxf.new(DXF_VERSION)
    doc.header["$INSUNITS"] =  meters_unit_code(target_crs)
    msp = doc.modelspace()

    # Create a dedicated layer for geometry and another for attribute labels.
    geom_layer = "SHP_GEOMETRY"
    text_layer = "SHP_ATTRIBUTES"
    doc.layers.new(geom_layer, dxfattribs={"color": geom_color})
    if label_fields:
        doc.layers.new(text_layer, dxfattribs={"color": text_color})
        # Use an explicit text style so AutoCAD can resolve the selected font.
        if "SHP_TEXT_STYLE" not in doc.styles:
            doc.styles.new("SHP_TEXT_STYLE", dxfattribs={"font": font_file})

    total = len(reader)
    geom_count = 0
    text_count = 0
    for i, sr in enumerate(reader.iterShapeRecords()):
        if sr.shape.shapeType == shapefile.NULL:
            continue
        geom = shape(sr.shape.__geo_interface__)
        geom = shapely_transform(transformer.transform, geom)
        geom_count += add_geometry(msp, geom, geom_layer)

        if label_fields and not geom.is_empty:
            x, y = label_anchor(geom)
            vals = dict(zip(field_names, list(sr.record)))
            label = " | ".join(
                field_text(vals.get(name, ""))
                for name in label_fields
                if field_text(vals.get(name, ""))
            )
            if label:
                # Small default height in target CRS units; user can adjust in CAD.
                msp.add_text(
                    label[:240],
                    dxfattribs={
                        "layer": text_layer,
                        "height": float(text_height),
                        "style": "SHP_TEXT_STYLE",
                        "insert": (x, y),
                    },
                )
                text_count += 1
        if i % 100 == 0 or i == total - 1:
            progress_cb(i + 1, total)

    doc.saveas(out_path)
    reader.close()
    return total, geom_count, text_count, source_crs.to_string(), target_crs.to_string()


def meters_unit_code(crs):
    # DXF INSUNITS: 6 = meters, 4 = millimeters, 2 = feet, 0 = unitless.
    try:
        axis = crs.axis_info[0]
        unit = (axis.unit_name or "").lower()
        if "metre" in unit or "meter" in unit:
            return 6
        if "foot" in unit or "feet" in unit:
            return 2
        if "millimet" in unit:
            return 4
    except Exception:
        pass
    return 0


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("760x650")
        self.minsize(700, 600)
        self.shp_path = tk.StringVar()
        self.out_path = tk.StringVar()
        self.target_epsg = tk.StringVar(value="EPSG:5179")
        self.encoding = tk.StringVar(value="cp949")
        self.geom_color = tk.StringVar(value="흰색(7)")
        self.text_color = tk.StringVar(value="노랑(2)")
        self.font_name = tk.StringVar(value="맑은 고딕")
        self.text_height = tk.StringVar(value="1.5")
        self.status = tk.StringVar(value="SHP 파일을 선택해 주세요.")
        self.field_vars = {}
        self._build()

    def _build(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="SHP → DXF Converter", font=("맑은 고딕", 18, "bold")).pack(anchor="w")
        ttk.Label(root, text="연속지적도 SHP를 AutoCAD 2007 DXF로 변환합니다.",
                  foreground="#555").pack(anchor="w", pady=(2, 14))

        filebox = ttk.LabelFrame(root, text="1. 입력 / 출력 파일", padding=10)
        filebox.pack(fill="x", pady=5)
        self._file_row(filebox, "SHP 파일", self.shp_path, self.pick_shp)
        self._file_row(filebox, "DXF 저장", self.out_path, self.pick_out)

        settings = ttk.LabelFrame(root, text="2. 좌표계 및 데이터 설정", padding=10)
        settings.pack(fill="x", pady=5)
        row = ttk.Frame(settings); row.pack(fill="x", pady=4)
        ttk.Label(row, text="변환할 좌표계 (EPSG 코드)", width=25).pack(side="left")
        ttk.Entry(row, textvariable=self.target_epsg, width=22).pack(side="left")
        ttk.Label(row, text="예: EPSG:5179 / EPSG:5186 / EPSG:4326",
                  foreground="#666").pack(side="left", padx=8)
        row = ttk.Frame(settings); row.pack(fill="x", pady=4)
        ttk.Label(row, text="SHP 문자 인코딩", width=25).pack(side="left")
        ttk.Combobox(row, textvariable=self.encoding, values=["cp949", "utf-8", "euc-kr"],
                     state="readonly", width=18).pack(side="left")
        ttk.Label(row, text="원본 좌표계는 SHP와 같은 이름의 .prj 파일에서 읽습니다.",
                  foreground="#666").pack(side="left", padx=8)

        stylebox = ttk.LabelFrame(root, text="3. CAD 글꼴 및 색상 설정", padding=10)
        stylebox.pack(fill="x", pady=5)
        row = ttk.Frame(stylebox); row.pack(fill="x", pady=4)
        ttk.Label(row, text="도형 색상", width=18).pack(side="left")
        ttk.Combobox(row, textvariable=self.geom_color, values=list(ACI_COLORS.keys()),
                     state="readonly", width=16).pack(side="left")
        ttk.Label(row, text="속성 문자 색상", width=16).pack(side="left", padx=(18, 0))
        ttk.Combobox(row, textvariable=self.text_color, values=list(ACI_COLORS.keys()),
                     state="readonly", width=16).pack(side="left")
        row = ttk.Frame(stylebox); row.pack(fill="x", pady=4)
        ttk.Label(row, text="속성 문자 글꼴", width=18).pack(side="left")
        ttk.Combobox(row, textvariable=self.font_name, values=list(FONT_OPTIONS.keys()),
                     state="readonly", width=16).pack(side="left")
        ttk.Label(row, text="문자 높이", width=16).pack(side="left", padx=(18, 0))
        ttk.Entry(row, textvariable=self.text_height, width=10).pack(side="left")
        ttk.Label(row, text="(도면 좌표 단위)", foreground="#666").pack(side="left", padx=6)

        attrbox = ttk.LabelFrame(root, text="4. CAD 문자로 표시할 속성정보", padding=10)
        attrbox.pack(fill="both", expand=True, pady=5)
        ttk.Label(attrbox, text="변환 후 SHP_ATTRIBUTES 레이어에 선택한 필드를 문자로 표시합니다.").pack(anchor="w")
        self.fields_frame = ttk.Frame(attrbox)
        self.fields_frame.pack(fill="both", expand=True, pady=6)
        self.fields_hint = ttk.Label(self.fields_frame, text="먼저 SHP 파일을 선택하면 속성 필드가 표시됩니다.",
                                     foreground="#666")
        self.fields_hint.pack(anchor="w")

        bottom = ttk.Frame(root); bottom.pack(fill="x", pady=(8, 0))
        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.pack(fill="x", pady=(0, 8))
        ttk.Label(bottom, textvariable=self.status).pack(anchor="w")
        self.convert_btn = ttk.Button(bottom, text="DXF 변환 시작", command=self.start_convert)
        self.convert_btn.pack(anchor="e", pady=(8, 0))

        note = ("주의: 속성 문자는 각 객체의 대표 위치에 배치됩니다. 지번이 많은 연속지적도는 "
                "문자가 겹칠 수 있으므로 CAD에서 문자 레이어를 조정하세요. 원본 SHP는 변경하지 않습니다.")
        ttk.Label(root, text=note, foreground="#8a4b00", wraplength=700).pack(anchor="w", pady=(10, 0))

    def _file_row(self, parent, label, var, command):
        row = ttk.Frame(parent); row.pack(fill="x", pady=4)
        ttk.Label(row, text=label, width=12).pack(side="left")
        ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(row, text="찾아보기", command=command).pack(side="left")

    def pick_shp(self):
        path = filedialog.askopenfilename(
            title="SHP 파일 선택", filetypes=[("Shapefile", "*.shp"), ("모든 파일", "*.*")]
        )
        if not path:
            return
        self.shp_path.set(path)
        if not self.out_path.get():
            self.out_path.set(os.path.splitext(path)[0] + ".dxf")
        self.load_fields(path)

    def load_fields(self, path):
        try:
            r = shapefile.Reader(path, encoding=self.encoding.get())
            fields = [f[0] for f in r.fields[1:]]
            r.close()
        except Exception as e:
            messagebox.showerror("SHP 읽기 오류", str(e)); return
        for w in self.fields_frame.winfo_children():
            w.destroy()
        self.field_vars.clear()
        if not fields:
            ttk.Label(self.fields_frame, text="속성 필드가 없습니다.").pack(anchor="w")
            return
        # Scrollable checkbox area
        canvas = tk.Canvas(self.fields_frame, height=130, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.fields_frame, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True); scrollbar.pack(side="right", fill="y")
        for idx, name in enumerate(fields):
            var = tk.BooleanVar(value=(name.upper() in ("PNU", "JIBUN", "PARCEL_NO", "A1")))
            self.field_vars[name] = var
            ttk.Checkbutton(inner, text=name, variable=var).grid(row=idx // 3, column=idx % 3, sticky="w", padx=8, pady=2)

    def pick_out(self):
        path = filedialog.asksaveasfilename(
            title="DXF 저장 위치", defaultextension=".dxf",
            filetypes=[("DXF 파일", "*.dxf")]
        )
        if path:
            self.out_path.set(path)

    def start_convert(self):
        shp = self.shp_path.get().strip()
        out = self.out_path.get().strip()
        epsg = self.target_epsg.get().strip()
        if not shp or not os.path.isfile(shp):
            messagebox.showwarning("확인", "변환할 SHP 파일을 선택해 주세요."); return
        if not out:
            messagebox.showwarning("확인", "DXF 저장 경로를 지정해 주세요."); return
        if not epsg:
            messagebox.showwarning("확인", "변환할 좌표계를 입력해 주세요."); return
        selected = [k for k, v in self.field_vars.items() if v.get()]
        self.convert_btn.configure(state="disabled")
        self.progress["value"] = 0
        self.status.set("변환 준비 중...")
        threading.Thread(target=self._worker, args=(shp, out, epsg, selected), daemon=True).start()

    def _worker(self, shp, out, epsg, selected):
        def progress(done, total):
            self.after(0, lambda: self._progress(done, total))
        try:
            try:
                height = float(self.text_height.get())
                if height <= 0:
                    raise ValueError("문자 높이는 0보다 큰 숫자여야 합니다.")
            except Exception as e:
                raise ValueError("문자 높이를 확인해 주세요. 예: 1.5") from e
            result = convert(
                shp, out, epsg, selected, self.encoding.get(), progress,
                ACI_COLORS[self.geom_color.get()],
                ACI_COLORS[self.text_color.get()],
                FONT_OPTIONS[self.font_name.get()],
                height,
            )
            self.after(0, lambda: self._success(out, result))
        except Exception as e:
            self.after(0, lambda: self._failure(str(e)))

    def _progress(self, done, total):
        self.progress["maximum"] = max(total, 1)
        self.progress["value"] = done
        self.status.set(f"변환 중... {done:,} / {total:,}개 객체")

    def _success(self, out, result):
        total, geoms, texts, src, dst = result
        self.convert_btn.configure(state="normal")
        self.status.set("변환 완료")
        messagebox.showinfo(
            "변환 완료",
            f"DXF 저장이 완료되었습니다.\n\n파일: {out}\n전체 레코드: {total:,}\n"
            f"생성 도형: {geoms:,}\n속성 문자: {texts:,}\n\n"
            f"원본 좌표계: {src}\n변환 좌표계: {dst}\n\n"
            "AutoCAD에서 DXF를 열어 결과를 확인해 주세요."
        )

    def _failure(self, error):
        self.convert_btn.configure(state="normal")
        self.status.set("변환 실패")
        messagebox.showerror("변환 실패", error)


if __name__ == "__main__":
    App().mainloop()
