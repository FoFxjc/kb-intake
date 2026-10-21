"""The fixed vocabularies of contract v2 (intake-design-v2 §3, §5): region types, workflow handlers, region statuses,
gap codes, and the two mappings from model labels to region types.

Every module takes these names from here; the JSON Schemas in contract/v2 list the same values.
"""

# layout-design §2 as used by the v2 workflow (§3); `screenshot` is folded into `diagram` (§5.11)
REGION_TYPES = ("title", "heading", "text", "list", "caption", "footnote", "formula", "toc", "table", "chart",
                "diagram", "picture", "logo", "icon", "signature", "stamp", "page_header", "page_footer", "unknown")

HANDLERS = ("text", "table_structure", "record", "discard", "unhandled", "ocr")

# region status after extraction (§5.4); "labeled" only exists in the work directory, before extraction
STATUSES = ("extracted", "discarded", "recorded", "excluded", "unhandled")

LABEL_SOURCES = ("model", "user", "native")

GAP_CODES = ("table_suspected", "low_confidence", "table_words_unassigned", "table_unmatched", "unhandled",
             "no_text_layer", "lang_mismatch", "extractor_degraded")

LOW_CONFIDENCE = 0.5          # default threshold for layout score and classifier top score (§5.7)

# Docling layout labels -> region types. Labels not listed (form, key_value_region, checkbox_*) are "unknown".
DOCLING_TYPE = {
    "title": "title", "section_header": "heading", "text": "text", "paragraph": "text", "reference": "text",
    "list_item": "list", "caption": "caption", "footnote": "footnote", "page_header": "page_header",
    "page_footer": "page_footer", "formula": "formula", "code": "text", "document_index": "toc", "table": "table",
    "picture": "picture", "chart": "chart",
}

# DocumentFigureClassifier-v2.5 classes -> (type, subtype) for a `picture` region (§5.11). Classes not listed stay
# (picture, None). "table" also stays a picture but raises a table_suspected gap with source "classifier".
CLASSIFIER_TYPE = {
    "bar_chart": ("chart", "bar"), "line_chart": ("chart", "line"), "pie_chart": ("chart", "pie"),
    "flow_chart": ("chart", "flow"), "geographical_map": ("chart", "map"), "topographical_map": ("chart", "map"),
    "scatter_plot": ("chart", "other"), "box_plot": ("chart", "other"),
    "logo": ("logo", None), "icon": ("icon", None), "signature": ("signature", None), "stamp": ("stamp", None),
    "screenshot_from_computer": ("diagram", None), "screenshot_from_manual": ("diagram", None),
    "engineering_drawing": ("diagram", None),
}
CLASSIFIER_TABLE = "table"


def docling_type(label: str | None) -> str:
    return DOCLING_TYPE.get(label or "", "unknown")


def classified_type(class_name: str | None) -> tuple[str, str | None]:
    return CLASSIFIER_TYPE.get(class_name or "", ("picture", None))
