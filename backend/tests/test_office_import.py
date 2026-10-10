import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from agent.db import Database
from agent.html_document import index_markdown, page
from agent.llm import UserError
from agent.office_converter import PROGIDS, VISIO_OPEN, OfficeConverter, capabilities, convert_with_office
from agent.office_html import PLAIN_CELLS, excel_html, format_value, powerpoint_html, visio_html
from agent.office_import import POWERPOINT_NOTICE, OfficeImporter
from agent.office_session import OfficeSession, Patient, property_with, step
from agent.service import Service
from test_rtf_import import FakeLLM, fake_com, fake_modules, wait

PAGE = page("업무 보고", "<h2>개발 계획</h2><table><tr><td>담당자</td><td>홍길동</td></tr></table>", "word")


@pytest.fixture
def importer(tmp_path):
    service = Service(Database(tmp_path / "data" / "wiki.db"), FakeLLM())
    service.office = OfficeImporter(service, converter=lambda *args: PAGE, capability_check=lambda: {"available": True})
    return service.office


@pytest.mark.parametrize(
    "extension,application",
    [
        (".ppt", "PowerPoint"),
        (".PPTX", "PowerPoint"),
        (".pptm", "PowerPoint"),
        (".xls", "Excel"),
        (".xlsx", "Excel"),
        (".xlsm", "Excel"),
        (".xlsb", "Excel"),
        (".doc", "Word"),
        (".docx", "Word"),
        (".docm", "Word"),
        (".vsd", "Visio"),
        (".vsdx", "Visio"),
        (".VSDM", "Visio"),
    ],
)
def test_import_search_html_edit_restart_and_delete(importer, tmp_path, extension, application):
    source = tmp_path / ("업무 보고" + extension)
    source.write_bytes(b"office data")
    importer.service.dispatch("office.start", {"path": str(source), "mode": "file"})
    result = wait(importer)
    assert (result["imported"], result["failed"]) == (1, 0)
    ident = result["results"][0]["doc_id"]
    doc = importer.service.wiki_get({"id": ident})
    assert doc["title"] == "업무 보고"
    assert doc["source"]["kind"] == "office" and doc["source"]["application"] == application
    assert doc["source"]["source_path"] == str(source)
    assert "html_path" not in doc["source"]
    assert (doc["format"], doc["html"]) == ("html", PAGE)
    assert doc["content"] == index_markdown(PAGE) == "## 개발 계획\n\n|  |  |\n| --- | --- |\n| 담당자 | 홍길동 |"
    assert not (Path(importer.service.db.path).parent / "office").exists()
    assert importer.service.wiki_source_html({"id": ident})["html"] == PAGE
    assert importer.service.wiki_search({"query": "홍길동", "mode": "word"})["documents"][0]["id"] == ident
    assert "html" not in importer.service.wiki_list({})[0]
    assert source.read_bytes() == b"office data"
    importer.service.wiki_save({"id": ident, "title": "수정", "content": "사용자 편집"})
    restarted = Service(Database(importer.service.db.path), FakeLLM())
    assert restarted.wiki_get({"id": ident})["content"] == "사용자 편집"
    assert restarted.wiki_source_html({"id": ident}) == {"html": PAGE, "filename": "수정.html"}
    restarted.wiki_delete({"id": ident})
    restarted.wiki_purge({"ids": [ident]})
    with restarted.db.connect() as db:
        assert db.execute("SELECT count(*) FROM office_sources").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM document_html").fetchone()[0] == 0
    assert not (Path(importer.service.db.path).parent / "office").exists()


def test_mixed_folder_duplicates_changes_and_per_file_failure(importer, tmp_path):
    folder = tmp_path / "documents"
    folder.mkdir()
    (folder / "nested").mkdir()
    for name, value in {
        "a.docx": b"same",
        "b.DOCX": b"same",
        "bad.xlsx": b"broken",
        "~$lock.docx": b"lock",
        "ignore.pdf": b"pdf",
        "nested/c.pptx": b"slides",
        "nested/d.vsdx": b"diagram",
    }.items():
        (folder / name).write_bytes(value)
    seen = []

    def convert(source, *_):
        seen.append(source.suffix)
        if source.read_bytes() == b"broken":
            raise UserError("손상된 Excel 파일")
        # Changed bytes are changed text here: a file whose text stayed keeps its document (sources.plan).
        return page("업무 보고", "<p>바뀐 계획</p>", "word") if source.read_bytes() == b"modified" else PAGE

    importer.converter = convert
    importer.start({"path": str(folder), "mode": "directory", "recursive": True})
    result = wait(importer)
    assert (result["total"], result["imported"], result["skipped"], result["failed"]) == (5, 3, 1, 1)
    assert seen == [".docx", ".xlsx", ".pptx", ".vsdx"]
    (folder / "a.docx").write_bytes(b"modified")
    importer.start({"path": str(folder), "mode": "directory", "recursive": False})
    result = wait(importer)
    # The changed file updates its document; the copy that had the same bytes is a document of its own now.
    assert (result["total"], result["imported"], result["updated"], result["skipped"]) == (3, 1, 1, 0)
    assert len(importer.service.wiki_list({})) == 4


def test_powerpoint_notice_is_raised_once_a_presentation_is_about_to_be_read(importer, tmp_path):
    folder = tmp_path / "발표"
    folder.mkdir()
    for name, value in {"a.docx": b"doc", "b.pptx": b"slides", "c.pptx": b"slides", "d.PPTM": b"more"}.items():
        (folder / name).write_bytes(value)
    seen = []

    def convert(source, *_):
        seen.append((source.name, importer.status()["notice"]))
        return PAGE

    importer.converter = convert
    assert importer.start({"path": str(folder), "mode": "directory"})["notice"] == ""
    job = wait(importer)
    # Nothing to tell before a Word file; the notice is up before the first presentation is read.
    assert seen == [("a.docx", ""), ("b.pptx", POWERPOINT_NOTICE), ("d.PPTM", POWERPOINT_NOTICE)]
    assert job["notice"] == POWERPOINT_NOTICE and job["skipped"] == 1
    # A presentation that was imported before is skipped without being read, so no notice.
    importer.start({"path": str(folder / "c.pptx"), "mode": "file"})
    job = wait(importer)
    assert (job["skipped"], job["notice"]) == (1, "")


def test_cancel_remains_responsive_and_does_not_persist_partial_file(importer, tmp_path):
    source = tmp_path / "cancel.docx"
    source.write_bytes(b"source")
    entered = threading.Event()

    def convert(source, cancel):
        entered.set()
        assert cancel.wait(3)
        return PAGE

    importer.converter = convert
    importer.start({"path": str(source), "mode": "file"})
    assert entered.wait(3)
    assert importer.service.dispatch("health", {}) == {"ok": True}
    assert importer.service.dispatch("office.status", {})["status"] == "running"
    with pytest.raises(UserError, match="이미"):
        importer.start({"path": str(source), "mode": "file"})
    importer.service.dispatch("office.cancel", {})
    assert wait(importer)["status"] == "cancelled"
    assert importer.service.wiki_list({}) == []
    assert list(Path(importer.service.db.path).parent.glob("office/*")) == []


def test_cleared_results_stay_cleared_until_the_next_job(importer, tmp_path):
    source = tmp_path / "clear.docx"
    source.write_bytes(b"source")
    entered, release = threading.Event(), threading.Event()

    def convert(source, cancel):
        entered.set()
        assert release.wait(3)
        return PAGE

    importer.converter = convert
    job = importer.service.dispatch("office.start", {"path": str(source), "mode": "file"})
    assert entered.wait(3)
    # A running job keeps showing its progress.
    assert "dismissed" not in importer.service.dispatch("office.dismiss", {"id": job["id"]})
    release.set()
    assert wait(importer)["status"] == "completed"
    # Only the job that was cleared: a screen still showing an older job cannot clear a newer one.
    assert "dismissed" not in importer.service.dispatch("office.dismiss", {"id": "older job"})
    assert importer.service.dispatch("office.dismiss", {"id": job["id"]})["dismissed"] is True
    assert importer.service.dispatch("office.status", {})["dismissed"] is True
    importer.converter = lambda *args: PAGE
    assert "dismissed" not in importer.service.dispatch("office.start", {"path": str(source), "mode": "file"})
    assert wait(importer)["skipped"] == 1
    # The RTF importer takes the same request; it has no job here.
    assert importer.service.dispatch("rtf.dismiss", {"id": job["id"]}) is None


def test_analysis_failure_keeps_the_page_and_document(importer, tmp_path):
    source = tmp_path / "analyze.xlsx"
    source.write_bytes(b"data")

    def fail(_):
        raise UserError("LLM 오프라인")

    importer.service.wiki_analyze = fail
    importer.start({"path": str(source), "mode": "file", "analyze": True})
    job = wait(importer)
    assert (job["imported"], job["failed"], job["analysis_failed"]) == (1, 0, 1)
    assert importer.service.wiki_source_html({"id": job["results"][0]["doc_id"]})["html"] == PAGE


def test_database_failure_rolls_back_document_without_files(importer, tmp_path):
    source = tmp_path / "rollback.docx"
    source.write_bytes(b"data")
    with importer.service.db.connect() as db:
        db.execute(
            "CREATE TRIGGER fail_office BEFORE INSERT ON office_sources BEGIN SELECT RAISE(ABORT, 'failure'); END"
        )
    importer.start({"path": str(source), "mode": "file"})
    assert wait(importer)["failed"] == 1
    assert importer.service.wiki_list({}) == []
    assert list(Path(importer.service.db.path).parent.glob("office/*")) == []


@pytest.mark.parametrize(
    "html",
    ["", " " * (10 * 1024 * 1024 + 1), page("빈 문서", "<p><br></p>", "word")],
    ids=["empty", "blank", "nothing-shown"],
)
def test_empty_content_is_rejected(importer, tmp_path, monkeypatch, html):
    source = tmp_path / "invalid.docx"
    source.write_bytes(b"data")
    importer.converter = lambda *args: html
    importer.start({"path": str(source), "mode": "file"})
    assert wait(importer)["failed"] == 1
    assert importer.service.wiki_list({}) == []


def test_a_long_presentation_is_saved_in_parts_that_each_name_the_application(importer, tmp_path):
    slides = "".join(
        f'<section class="slide"><h2>슬라이드 {i}</h2><p>{"발표 내용 " * 300}</p></section>' for i in range(60)
    )
    importer.converter = lambda *args: page("발표", slides, "powerpoint")
    source = tmp_path / "발표.pptx"
    source.write_bytes(b"slides")
    importer.start({"path": str(source), "mode": "file"})
    result = wait(importer)["results"][0]
    docs = [importer.service.wiki_get({"id": ident}) for ident in result["doc_ids"]]
    assert len(docs) == 2 and result["message"] == "글이 80,000자를 넘어 HTML 문서 2개로 나누어 Wiki에 저장했습니다."
    assert [(doc["source"]["application"], doc["source"]["part"], doc["source"]["parts"]) for doc in docs] == [
        ("PowerPoint", 1, 2),
        ("PowerPoint", 2, 2),
    ]
    # Slides are cut between, never inside: each part holds whole slides.
    assert [doc["html"].count('<section class="slide">') for doc in docs] == [
        doc["content"].count("## 슬라이드") for doc in docs
    ]
    assert sum(doc["content"].count("## 슬라이드") for doc in docs) == 60


def test_file_limits_capabilities_and_schema_upgrade(importer, tmp_path, monkeypatch):
    source = tmp_path / "large.docx"
    source.write_bytes(b"data")
    monkeypatch.setattr("agent.file_import.MAX_FILE_BYTES", 3)
    importer.start({"path": str(source), "mode": "file"})
    assert "50MB" in wait(importer)["results"][0]["message"]
    importer.capability_check = lambda: {"available": False, "reason": "Office 설치 필요"}
    with pytest.raises(UserError, match="Office 설치"):
        importer.start({"path": str(source), "mode": "file"})
    if sys.platform != "win32":
        assert capabilities()["available"] is False
    saved = importer.service.wiki_save({"title": "기존 문서", "content": "보존"})
    with importer.service.db.connect() as db:
        db.execute("DROP TABLE office_sources")
    upgraded = Service(Database(importer.service.db.path), FakeLLM())
    assert upgraded.wiki_get({"id": saved["id"]})["content"] == "보존"


def collection(items):
    return NS(Count=len(items), Item=lambda index: items[index - 1])


def text_shape(text, **kwargs):
    return NS(
        Type=1,
        HasTable=False,
        HasSmartArt=False,
        HasTextFrame=True,
        TextFrame=NS(HasText=bool(text), TextRange=NS(Text=text)),
        **kwargs,
    )


def placeholder(kind, text):
    shape = text_shape(text, PlaceholderFormat=NS(Type=kind))
    shape.Type = 14  # msoPlaceholder
    return shape


PLAIN_SPREADSHEET = (
    '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet><Table/></Worksheet></Workbook>'
)


def used_range(top, left, value2, *, value=None, formula=None, xml=PLAIN_SPREADSHEET):
    """A UsedRange as COM returns it: a scalar for one cell, rows of values otherwise; Value(11) gives `xml`, here
    cells without formatting of their own."""
    single = len(value2) == len(value2[0]) == 1

    def grid(rows):
        return rows[0][0] if single else rows

    formula = formula or tuple(tuple("" if cell is None else str(cell) for cell in row) for row in value2)
    return NS(
        Rows=NS(Count=len(value2)),
        Columns=NS(Count=len(value2[0])),
        Row=top,
        Column=left,
        Value2=grid(value2),
        Value=grid(value or value2),
        Formula=grid(formula),
        _oleobj_=SpreadsheetDispatch(xml, []),
    )


def workbook_fixture():
    # A1:C3 where row 2 and column B hold nothing but formatting.
    report = used_range(
        1,
        1,
        (("<담당자>", None, "홍길동"), (None, None, None), (0.1 + 0.2, None, 42.0)),
        formula=(("<담당자>", "", "홍길동"), ("", "", ""), ("0.3", "", "=SUM(A1:A2)")),
    )
    return NS(
        Worksheets=collection(
            [
                NS(Name="개발 & 운영", Visible=-1, UsedRange=report),
                NS(Name="요약", Visible=0, UsedRange=used_range(5, 4, ((False,),))),
                NS(Name="빈 시트", Visible=-1, UsedRange=used_range(1, 1, ((None,),))),
            ]
        )
    )


def markdown(body, kind="excel"):
    return index_markdown(page("문서", body, kind))


def test_excel_keeps_cell_addresses_and_leaves_out_empty_rows_and_columns():
    ticks = []
    result = markdown(excel_html(workbook_fixture(), lambda: ticks.append(1)))
    assert (
        "## 개발 \\& 운영\n\n|  | A | C |\n| --- | --- | --- |\n| 1 | \\<담당자\\> | 홍길동 |\n"
        "| 3 | 0.3 | 42 수식: `=SUM(A1:A2)` |" in result
    )
    assert "## 요약 (숨긴 시트)\n\n|  | D |\n| --- | --- |\n| 5 | FALSE |" in result
    assert "빈 시트" not in result and len(ticks) == 3
    # A workbook beyond 100,000 cells is read up to there: the rows of a sheet that still fit, and the page says so.
    workbook = workbook_fixture()
    first = workbook.Worksheets.Item(1)
    first.UsedRange.Rows.Count = 100001
    asked = []
    first.Range = lambda address: (
        asked.append(address) or used_range(1, 1, (("<담당자>", None, "홍길동"),) + ((None, None, None),) * 33332)
    )
    result = markdown(excel_html(workbook))
    assert asked == ["A1:C33333"]
    assert (
        "## 개발 \\& 운영\n\n시트가 커서 100,001행 중 앞 33,333행만 가져왔습니다 (시트 사용 범위 합계 "
        "100,000셀까지).\n\n"
        "|  | A | C |\n| --- | --- | --- |\n| 1 | \\<담당자\\> | 홍길동 |"
    ) in result
    assert "## 요약 (숨긴 시트)\n\n|  | D |" in result
    # An empty sheet has no cells to leave out: it is not listed, even once the cells are used up.
    assert result.endswith("| 5 | FALSE |") and "빈 시트" not in result
    # Its used range is A1 alone, without a value, and takes none of the cells: the next sheet's rows all fit.
    empty = NS(Name="빈 시트", Visible=-1, UsedRange=used_range(1, 1, ((None,),)))
    full = NS(Name="가득", Visible=-1, UsedRange=used_range(1, 1, (("값",),) + ((None,),) * 99999))
    result = markdown(excel_html(NS(Worksheets=collection([empty, full]))))
    assert result == "## 가득\n\n|  | A |\n| --- | --- |\n| 1 | 값 |"


def test_excel_values_read_as_excel_shows_them():
    utc = timezone.utc  # pywin32 returns COM dates with a UTC zone
    used = used_range(
        2,
        2,
        ((46293.0, 0.3958333333, 46293.5729166667, -2146826281, 12.345678, True),),
        value=(
            (
                datetime(2026, 9, 28, tzinfo=utc),
                datetime(1899, 12, 30, 9, 30, tzinfo=utc),
                datetime(2026, 9, 28, 13, 45, tzinfo=utc),
                -2146826281,
                Decimal("12.3457"),
                True,
            ),
        ),
        formula=(("46293", "0.395833333333333", "46293.5729166667", "=1/0", "12.345678", "TRUE"),),
    )
    result = markdown(excel_html(NS(Worksheets=collection([NS(Name="값", Visible=-1, UsedRange=used)]))))
    assert "| 2 | 2026-09-28 | 09:30 | 2026-09-28 13:45 | #DIV/0! 수식: `=1/0` | 12.345678 | TRUE |" in result
    assert "|  | B | C | D | E | F | G |" in result
    assert "12.3457 " not in result and "+00:00" not in result


def presentation_fixture():
    table = NS(
        Rows=NS(Count=2),
        Columns=NS(Count=2),
        FirstRow=True,
        Cell=lambda r, c: NS(Shape=text_shape([["담당", "이름"], ["개발", "홍길동"]][r - 1][c - 1])),
    )
    shapes = [
        text_shape("슬라이드 본문\r두 번째 줄"),
        placeholder(1, "개발 계획\v2026"),
        NS(Type=19, HasTable=True, Table=table),
        NS(Type=6, GroupItems=collection([text_shape("그룹 텍스트")])),
        NS(
            Type=24,
            HasTable=False,
            HasSmartArt=True,
            SmartArt=NS(AllNodes=collection([NS(TextFrame2=NS(TextRange=NS(Text="SmartArt 텍스트")))])),
        ),
        placeholder(7, ""),
    ]
    notes = collection([placeholder(2, "발표할 내용"), placeholder(15, "노트 바닥글")])
    plain_table = NS(
        Rows=NS(Count=1), Columns=NS(Count=2), FirstRow=False, Cell=lambda r, c: NS(Shape=text_shape(["a", "b"][c - 1]))
    )
    second = NS(Shapes=collection([NS(Type=19, HasTable=True, Table=plain_table)]), NotesPage=NS(Shapes=collection([])))
    return NS(Slides=collection([NS(Shapes=collection(shapes), NotesPage=NS(Shapes=notes)), second]))


def test_powerpoint_titles_tables_groups_smartart_and_speaker_notes():
    ticks = []
    result = markdown(powerpoint_html(presentation_fixture(), lambda: ticks.append(1)), "powerpoint")
    assert result.startswith(
        "## 슬라이드 1 · 개발 계획 2026\n\n슬라이드 본문  \n두 번째 줄\n\n"
        "| 담당 | 이름 |\n| --- | --- |\n| 개발 | 홍길동 |\n\n그룹 텍스트\n\n- SmartArt 텍스트\n\n"
        "### 발표자 노트\n\n발표할 내용\n\n## 슬라이드 2\n\n|  |  |\n| --- | --- |\n| a | b |"
    )
    assert "노트 바닥글" not in result and len(ticks) == 2


def visio_shape(ident, text="", *, kind=3, one_d=False, connects=(), data=(), children=(), name=None, broken=False):
    def cells(section, row, column):
        assert section == 243
        label, value, hidden = data[row]

        def result(units):
            assert units == ""
            return {0: value, 2: label}[column]

        return NS(ResultIU=float(hidden), ResultStr=result, RowName=f"Row_{row + 1}")

    def exists(section, local):
        if broken:
            raise RuntimeError("ShapeSheet를 읽을 수 없음")
        return int(bool(data))

    return NS(
        ID=ident,
        Type=kind,
        Name=name or f"Shape.{ident}",
        Characters=NS(Text=text),
        OneD=-1 if one_d else 0,
        Connects=collection([NS(FromPart=part, ToSheet=NS(ID=target)) for part, target in connects]),
        SectionExists=exists,
        RowCount=lambda section: len(data),
        CellsSRC=cells,
        Shapes=collection(list(children)),
    )


def drawing_fixture():
    shapes = [
        visio_shape(1, "사용자 단말"),
        visio_shape(
            2,
            "API 서버",
            kind=2,
            data=[("담당자", "홍길동", 0), ("내부 코드", "X1", 1), ("", "10.0.0.1", 0)],
            children=[visio_shape(3, "인증 모듈")],
        ),
        visio_shape(4, "데이터베이스\u2028(주 DB)", broken=True),
        visio_shape(5, "HTTPS", one_d=True, connects=[(9, 1), (12, 2)]),  # visBegin, visEnd
        visio_shape(6, one_d=True, connects=[(7, 2)]),  # only its begin (visBeginX) is glued
        visio_shape(7, "안내선", kind=5),  # a guide
        visio_shape(8, name="Rectangle.8", kind=2, children=[visio_shape(9, "그룹 속 도형")]),
        visio_shape(10, "자유 선", one_d=True),
        visio_shape(11, one_d=True, connects=[(9, 4), (12, 8)]),
    ]
    return NS(
        Pages=collection(
            [
                NS(Name="시스템 구성도", Background=0, Shapes=collection(shapes)),
                NS(Name="배경-1", Background=-1, Shapes=collection([visio_shape(1, "대외비")])),
                NS(Name="빈 페이지", Background=0, Shapes=collection([visio_shape(1)])),
            ]
        )
    )


def test_visio_pages_shape_text_connections_and_shape_data():
    ticks = []
    result = markdown(visio_html(drawing_fixture(), lambda: ticks.append(1)), "visio")
    assert result.startswith(
        "## 페이지 1 · 시스템 구성도\n\n### 도형 텍스트\n\n- 사용자 단말\n- API 서버\n  - 인증 모듈\n"
        "- 데이터베이스  \n  (주 DB)\n- 그룹 속 도형\n- 자유 선\n\n### 연결\n\n"
        "- 사용자 단말 → API 서버: HTTPS\n- API 서버 → (미연결)\n- 데이터베이스 (주 DB) → Rectangle.8\n\n"
        "### 도형 데이터\n\n| 도형 | 항목 | 값 |\n| --- | --- | --- |\n| API 서버 | 담당자 | 홍길동 |\n"
        "| API 서버 | Row\\_3 | 10.0.0.1 |\n\n## 페이지 2 · 배경-1 (배경)\n\n### 도형 텍스트\n\n- 대외비"
    )
    assert "안내선" not in result and "내부 코드" not in result and "빈 페이지" not in result
    assert len(ticks) == 3


def test_visio_limits_shapes_and_nesting(monkeypatch):
    monkeypatch.setattr("agent.office_html.MAX_SHAPES", 3)
    with pytest.raises(UserError, match="Visio 도형"):
        visio_html(drawing_fixture())
    monkeypatch.undo()
    deep = visio_shape(0, "맨 아래")
    for ident in range(1, 40):
        deep = visio_shape(ident, kind=2, children=[deep])
    with pytest.raises(UserError, match="중첩"):
        visio_html(NS(Pages=collection([NS(Name="깊은 그룹", Background=0, Shapes=collection([deep]))])))


@pytest.mark.parametrize("extension", [".doc", ".docx", ".docm"])
def test_word_office_conversion_returns_the_page_without_files(monkeypatch, tmp_path, extension):
    calls, _, _ = fake_com(monkeypatch)
    result = convert_with_office(tmp_path / ("Word 보고서" + extension))
    assert "<title>Word 보고서</title>" in result and "| 일시 | 월요일 |" in index_markdown(result)
    assert list(tmp_path.iterdir()) == []
    assert "홍길동" in result and "<script>" not in result and "hidden css" not in result
    assert calls[-1][0] == "quit"


def excel_app(calls, *, fail=False, adopt=False):
    workbook = workbook_fixture()
    workbook.Close = lambda **kw: calls.append(("close", kw))
    app = NS(
        Visible=False,
        DisplayAlerts=True,
        ScreenUpdating=True,
        EnableEvents=True,
        AskToUpdateLinks=True,
        AutomationSecurity=1,
    )

    def settings():
        return (
            app.DisplayAlerts,
            app.ScreenUpdating,
            app.EnableEvents,
            app.AskToUpdateLinks,
            app.AutomationSecurity,
        )

    app.Quit = lambda **kw: calls.append(("quit", kw, settings()))

    def open_workbook(**kwargs):
        calls.append(("open", kwargs))
        assert app.Visible is False and settings() == (False, False, False, False, 3)
        if fail:
            raise RuntimeError("COM failed")
        app.Visible = adopt  # the user opens their own workbook in this Excel meanwhile
        return workbook

    app.Workbooks = NS(Open=open_workbook)
    return app


def test_excel_reads_a_readonly_workbook_hidden_and_puts_its_settings_back(monkeypatch, tmp_path):
    calls = []
    fake_modules(monkeypatch, excel_app(calls), calls)
    source = tmp_path / "보고서.xlsx"
    result = convert_with_office(source)
    assert '<div class="doc excel">' in result and "<title>보고서</title>" in result
    assert "| 1 | \\<담당자\\> | 홍길동 |" in index_markdown(result)
    opened = next(c[1] for c in calls if c[0] == "open")
    assert opened["Filename"] == str(source.resolve()) and opened["ReadOnly"] is True
    assert opened["UpdateLinks"] == 0 and opened["AddToMru"] is False and opened["Password"] == ""
    assert [c[0] for c in calls] == ["dispatch", "open", "close", "quit"]
    assert calls[0] == ("dispatch", "Excel.Application") and calls[2] == ("close", {"SaveChanges": False})
    # Excel saves AskToUpdateLinks as the user's preference, so it is back before Excel quits.
    # Alerts and screen updates stay off until then, so quitting shows nothing.
    assert calls[3] == ("quit", {}, (False, False, True, True, 1))
    assert list(tmp_path.iterdir()) == []


def test_failures_name_the_step_and_still_quit_the_owned_process(monkeypatch, tmp_path):
    calls = []
    fake_modules(monkeypatch, excel_app(calls, fail=True), calls)
    with pytest.raises(
        UserError,
        match=r"^Excel 파일 열기 실패: RuntimeError: COM failed\. Excel에서 직접 열리는지, "
        r"암호·DRM·보안 정책을 확인해 주세요\.$",
    ):
        convert_with_office(tmp_path / "손상.xlsx")
    assert calls[-1] == ("quit", {}, (False, False, True, True, 1))


def test_excel_the_user_opened_a_workbook_in_keeps_running_as_it_was(monkeypatch, tmp_path):
    calls = []
    app = excel_app(calls, adopt=True)
    fake_modules(monkeypatch, app, calls)
    convert_with_office(tmp_path / "보고서.xlsx")
    assert ("close", {"SaveChanges": False}) in calls and not any(c[0] == "quit" for c in calls)
    assert (app.DisplayAlerts, app.ScreenUpdating, app.EnableEvents, app.AskToUpdateLinks, app.AutomationSecurity) == (
        True,
        True,
        True,
        True,
        1,
    )


def powerpoint_app(calls, *, visible=0, running=(), windows=0, adopt=False):
    presentation = presentation_fixture()
    presentation.Close = lambda **kw: calls.append(("close", kw))
    presentation.Windows = NS(Count=windows)
    app = NS(Visible=visible, DisplayAlerts=2, AutomationSecurity=1, Quit=lambda **kw: calls.append(("quit", kw)))

    def open_presentation(**kwargs):
        calls.append(("open", kwargs, app.DisplayAlerts))
        assert app.AutomationSecurity == 3
        if adopt:
            app.Visible = -1  # the user opens PowerPoint, which joins this process
        return presentation

    others = [NS(FullName=name) for name in running]
    app.Presentations = NS(Count=len(others), Item=lambda index: others[index - 1], Open=open_presentation)
    return app


@pytest.mark.parametrize(
    "visible,running",
    [(0, []), (-1, []), (-1, ["C:\\발표\\다른 파일.pptx"]), (0, ["C:\\발표\\다른 프로그램이 연 파일.pptx"])],
)
def test_powerpoint_reads_without_a_window_and_quits_only_its_own_process(monkeypatch, tmp_path, visible, running):
    calls = []
    # PowerPoint runs one process per user: one that shows a window or holds presentations is someone's.
    app = powerpoint_app(calls, visible=visible, running=running)
    fake_modules(monkeypatch, app, calls)
    source = tmp_path / "발표.pptx"
    assert "## 슬라이드 1 · 개발 계획 2026" in index_markdown(convert_with_office(source))
    _, opened, alerts = next(c for c in calls if c[0] == "open")
    assert opened == {"FileName": str(source.resolve()), "ReadOnly": True, "Untitled": False, "WithWindow": False}
    assert ("close", {}) in calls and app.AutomationSecurity == 1
    if visible or running:
        # The user's PowerPoint keeps its alerts and keeps running; only the imported file closes.
        assert alerts == 2 and app.DisplayAlerts == 2
        assert not any(c[0] == "quit" for c in calls)
    else:
        assert alerts == 1  # ppAlertsNone
        assert calls[-1] == ("quit", {})


@pytest.mark.parametrize("already_listed,windows", [(True, 0), (False, 1)])
def test_powerpoint_never_closes_a_presentation_the_user_has_open(monkeypatch, tmp_path, already_listed, windows):
    source = tmp_path / "발표.pptx"
    calls = []
    app = powerpoint_app(calls, visible=-1, running=[str(source)] if already_listed else [], windows=windows)
    fake_modules(monkeypatch, app, calls)
    with pytest.raises(UserError, match="PowerPoint에 이 파일이 열려 있습니다"):
        convert_with_office(source)
    assert any(c[0] == "open" for c in calls) is not already_listed
    assert not any(c[0] in ("close", "quit") for c in calls) and app.AutomationSecurity == 1


def test_powerpoint_the_user_opened_meanwhile_keeps_running_as_it_was(monkeypatch, tmp_path):
    calls = []
    app = powerpoint_app(calls, adopt=True)
    fake_modules(monkeypatch, app, calls)
    convert_with_office(tmp_path / "발표.pptx")
    assert ("close", {}) in calls and not any(c[0] == "quit" for c in calls)
    assert (app.DisplayAlerts, app.AutomationSecurity) == (2, 1)


def visio_app(calls, *, fail=False, drawing=None):
    drawing = drawing or drawing_fixture()
    drawing.Saved = False
    drawing.Close = lambda: calls.append(("close", drawing.Saved))
    app = NS(Visible=False, AlertResponse=0, EventsEnabled=-1, ScreenUpdating=-1)
    app.Quit = lambda **kw: calls.append(("quit", kw, (app.AlertResponse, app.EventsEnabled, app.ScreenUpdating)))

    def open_drawing(path, flags):
        calls.append(("open", path, flags))
        assert (app.AlertResponse, app.EventsEnabled, app.ScreenUpdating) == (7, False, False)
        if fail:
            raise RuntimeError("COM failed")
        return drawing

    app.Documents = NS(OpenEx=open_drawing)
    return app


def test_visio_reads_a_readonly_drawing_in_an_invisible_visio(monkeypatch, tmp_path):
    calls = []
    fake_modules(monkeypatch, visio_app(calls), calls)
    source = tmp_path / "구성도.vsdx"
    assert "- 사용자 단말 → API 서버: HTTPS" in index_markdown(convert_with_office(source))
    # Read-only, not listed in recent files, macros disabled, without the saved workspace.
    assert VISIO_OPEN == 0x2 | 0x8 | 0x80 | 0x100
    assert calls[:2] == [("dispatch", "Visio.InvisibleApp"), ("open", str(source.resolve()), VISIO_OPEN)]
    # Document events are on again when Visio quits, while alerts are still answered with "No".
    assert calls[2:] == [("close", True), ("quit", {}, (7, -1, False))]


def test_visio_failure_names_the_step(monkeypatch, tmp_path):
    calls = []
    fake_modules(monkeypatch, visio_app(calls, fail=True), calls)
    with pytest.raises(UserError, match=r"^Visio 파일 열기 실패: RuntimeError: COM failed\."):
        convert_with_office(tmp_path / "구성도.vsd")
    assert calls[-1][0] == "quit"


class Busy(Exception):
    hresult, strerror = -2147418111, "호출 수신자가 호출을 거부했습니다."  # RPC_E_CALL_REJECTED


class Broken(Exception):
    hresult = -2147352567  # DISP_E_EXCEPTION


def test_calls_office_rejects_while_busy_are_repeated(monkeypatch):
    monkeypatch.setattr("agent.office_session.time.sleep", lambda seconds: None)
    attempts = []

    class Book:
        _oleobj_ = object()
        Name = "보고서"

    class Books:
        _oleobj_ = object()

        def Open(self, **kwargs):
            attempts.append(kwargs)
            if len(attempts) < 3:
                raise Busy()
            return Book()

        def Fail(self):
            attempts.append("fail")
            raise Broken()

    class App:
        _oleobj_ = object()
        Workbooks = Books()

    session = OfficeSession("Excel", PROGIDS["Excel"])
    app = Patient(App(), session)
    book = app.Workbooks.Open(Filename="보고서.xlsx")
    assert isinstance(book, Patient) and book.Name == "보고서" and len(attempts) == 3
    attempts.clear()
    with pytest.raises(Broken):
        app.Workbooks.Fail()
    assert attempts == ["fail"]
    attempts.clear()
    session.patience = 0
    with pytest.raises(Busy):
        app.Workbooks.Open(Filename="보고서.xlsx")
    assert len(attempts) == 1


def test_a_worker_says_which_office_it_may_have_left_running(monkeypatch, tmp_path):
    from agent.office_session import outcome
    from agent.word_converter import convert_with_word

    def refuse(*args, **kwargs):
        raise Broken()

    # A dialog rejects every call: the file stays open in a Word that did not quit, although the text was read.
    calls, word, doc = fake_com(monkeypatch)
    doc.Close = word.Quit = refuse
    result = outcome(lambda: convert_with_word(tmp_path / "in.rtf"), "Word 변환", "")
    assert result["ok"] and result["left"] == ["Word"]
    # A clean run says nothing of it; a Word that failed to start may have begun all the same.
    fake_com(monkeypatch)
    assert "left" not in outcome(lambda: convert_with_word(tmp_path / "in.rtf"), "Word 변환", "")
    fake_modules(monkeypatch, Broken(), [])
    result = outcome(lambda: convert_with_word(tmp_path / "in.rtf"), "Word 변환", "")
    assert not result["ok"] and result["left"] == ["Word"]

    def write():
        with OfficeSession("Word", PROGIDS["Word"], running=NS(Visible=True)) as office:
            office.track(object(), refuse)
        return {}

    assert "left" not in outcome(write, "원본 반영", "", key="summary")

    # A PowerPoint that does not say whether it is the user's is never quit; one started here is reported.
    class Silent(NS):
        @property
        def Visible(self):
            raise Broken()

    calls = []
    app = powerpoint_app(calls)
    fake_modules(monkeypatch, Silent(**{key: value for key, value in vars(app).items() if key != "Visible"}), calls)
    result = outcome(lambda: convert_with_office(tmp_path / "발표.pptx"), "Office 변환", "")
    assert result["error"].startswith("PowerPoint 실행 실패") and result["left"] == ["PowerPoint"]
    assert not any(call[0] == "quit" for call in calls)


def test_step_errors_carry_the_office_explanation():
    class ComError(Exception):
        hresult = -2147352567
        excepinfo = (0, "Microsoft Excel", "파일을 열 수 없습니다", None, 0, -2146827284)

    class WindowsError(Exception):
        winerror, strerror = 1400, "창 핸들이 잘못되었습니다."

    # Office's own description and the code it gave, as `stage 실패 [0x…]: description`.
    with pytest.raises(UserError, match=r"^Excel 파일 열기 실패 \[0x800A03EC\]: 파일을 열 수 없습니다\. 안내입니다\.$"):
        with step("Excel 파일 열기", "안내입니다."):
            raise ComError()
    with pytest.raises(UserError, match=r"^PowerPoint 실행 실패 \[0x00000578\]: 창 핸들이 잘못되었습니다\.$"):
        with step("PowerPoint 실행"):
            raise WindowsError()
    with pytest.raises(UserError, match=r"^Visio 내용 읽기 실패: KeyError: 'Shapes'\.$"):
        with step("Visio 내용 읽기"):
            raise KeyError("Shapes")
    # Still busy after every retry: most likely a dialog is open in that Office app.
    with pytest.raises(
        UserError,
        match=r"^PowerPoint 파일 열기 실패 \[0x80010001\]: 호출 수신자가 호출을 거부했습니다\. "
        r"Office 앱에 열린 대화 상자가 있다면 닫고 다시 시도해 주세요\.$",
    ):
        with step("PowerPoint 파일 열기", "무시되는 안내"):
            raise Busy()
    with pytest.raises(UserError, match="^그대로$"):
        with step("무시되는 단계"):
            raise UserError("그대로")


def stalled_worker(source, sender, stop, folder):
    time.sleep(30)


def test_worker_timeout_and_cancellation_clean_up_child_processes(tmp_path, monkeypatch):
    import multiprocessing

    monkeypatch.setattr(OfficeConverter, "check", lambda *args: None)
    monkeypatch.setattr(OfficeConverter, "worker", staticmethod(stalled_worker))
    folders = []
    real_mkdtemp = tempfile.mkdtemp
    monkeypatch.setattr(
        "agent.office_converter.tempfile.mkdtemp",
        lambda **kwargs: folders.append(real_mkdtemp(**kwargs)) or folders[-1],
    )
    before = {child.pid for child in multiprocessing.active_children()}
    for cancel, message in [(threading.Event(), "초과"), (threading.Event(), "취소")]:
        if message == "취소":
            cancel.set()
        with pytest.raises(UserError, match=message):
            OfficeConverter(timeout=0.2, grace=0.2)(tmp_path / "test.xlsx", cancel)
    assert {child.pid for child in multiprocessing.active_children()} == before
    # The scratch folder for the pictures Office exports goes even when the worker is killed.
    assert len(folders) == 2 and all(Path(folder).name.startswith("private-agent-office-") for folder in folders)
    assert not any(Path(folder).exists() for folder in folders)


def cooperative_worker(source, sender, stop, folder):
    Path(source + ".started").touch()
    if stop.wait(30):
        Path(source + ".stopped").touch()
    sender.close()


def page_worker(source, sender, stop, folder):
    sender.send({"ok": True, "html": PAGE})
    sender.close()


def test_a_file_waiting_for_its_office_app_says_so_in_the_import(tmp_path, monkeypatch):
    from agent.word_converter import TURNS, WAITING

    monkeypatch.setattr(OfficeConverter, "check", lambda *args: None)
    monkeypatch.setattr(OfficeConverter, "worker", staticmethod(page_worker))
    service = Service(Database(tmp_path / "data" / "wiki.db"), FakeLLM())
    importer = OfficeImporter(service, capability_check=lambda: {"available": True})
    source = tmp_path / "매출.xlsx"
    source.write_bytes(b"workbook")
    # Another import or write-back reads an Excel file meanwhile: this one waits for it, and says so.
    with TURNS.turn(["Excel"], threading.Event()):
        importer.start({"path": str(source), "mode": "file"})
        deadline = time.monotonic() + 10
        while importer.status()["waiting"] != WAITING.format(name="Excel") and time.monotonic() < deadline:
            time.sleep(0.05)
        assert importer.status()["waiting"] == WAITING.format(name="Excel")
        assert importer.status()["status"] == "running" and importer.status()["processed"] == 0
    importer.thread.join(30)
    job = importer.status()
    assert (job["status"], job["imported"], job["waiting"]) == ("completed", 1, "")


def test_a_stopped_worker_closes_its_office_itself_before_it_would_be_killed(tmp_path, monkeypatch):
    monkeypatch.setattr(OfficeConverter, "check", lambda *args: None)
    monkeypatch.setattr(OfficeConverter, "worker", staticmethod(cooperative_worker))
    source = tmp_path / "graceful.pptx"
    cancel = threading.Event()

    def cancel_once_started():
        deadline = time.monotonic() + 30
        while not Path(f"{source}.started").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        cancel.set()

    threading.Thread(target=cancel_once_started, daemon=True).start()
    with pytest.raises(UserError, match="취소"):
        OfficeConverter(timeout=60, grace=30)(source, cancel)
    assert Path(f"{source}.stopped").exists()


@pytest.mark.skipif(
    sys.platform != "win32" or os.getenv("PRIVATE_AGENT_OFFICE_TEST") != "1",
    reason="Requires Windows Office; opt in with PRIVATE_AGENT_OFFICE_TEST=1",
)
@pytest.mark.parametrize(
    "name,extension", [("Word", ".docx"), ("Excel", ".xlsx"), ("PowerPoint", ".pptx"), ("Visio", ".vsdx")]
)
def test_real_office_conversion(tmp_path, name, extension):
    if not capabilities()["applications"][name]:
        pytest.skip(f"{name} is not installed")
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    app = win32com.client.DispatchEx(PROGIDS[name])
    source = tmp_path / ("통합 테스트" + extension)
    try:
        if name == "Word":
            document = app.Documents.Add(Visible=False)
            document.Content.Text = "Office integration 한국어"
            # A floating oval with text (msoShapeOval), painted as an SVG picture.
            document.Shapes.AddShape(9, 72, 72, 144, 72).TextFrame.TextRange.Text = "도형 글"
            document.SaveAs2(str(source), FileFormat=12)
            document.Close(SaveChanges=0)
        elif name == "Excel":
            document = app.Workbooks.Add()
            sheet = document.Worksheets.Item(1)
            sheet.Cells(1, 1).Value = "Office integration 한국어"
            sheet.Cells(2, 1).Formula = "=1+2"
            sheet.Shapes.AddShape(1, 150, 40, 120, 50).TextFrame2.TextRange.Text = "도형 글"
            chart = sheet.ChartObjects().Add(150, 120, 240, 160).Chart
            chart.SetSourceData(sheet.Range("A2:A2"))
            document.SaveAs(str(source), FileFormat=51)
            document.Close(SaveChanges=False)
        elif name == "PowerPoint":
            document = app.Presentations.Add(WithWindow=False)
            slide = document.Slides.Add(1, 1)
            slide.Shapes.Title.TextFrame.TextRange.Text = "Office integration 한국어"
            slide.Shapes.AddShape(5, 100, 300, 200, 100)  # msoShapeRoundedRectangle, only in the slide's picture
            document.SaveAs(str(source), FileFormat=24)
            document.Close()
        else:
            document = app.Documents.Add("")
            page = document.Pages.Item(1)
            first, second = page.DrawRectangle(1, 1, 3, 2), page.DrawRectangle(5, 1, 7, 2)
            first.Text, second.Text = "Office integration 한국어", "연결 대상"
            first.AutoConnect(second, 0)  # visAutoConnectDirNone: glue a connector between the two
            document.SaveAs(str(source))
            document.Close()
    finally:
        app.Quit()
        pythoncom.CoUninitialize()
    result = OfficeConverter()(source, threading.Event())
    assert "Office integration 한국어" in result
    # Shapes and pictures arrive: painted from Word's XML, or as PowerPoint, Excel and Visio draw them.
    assert "그림 일부를 넣지 못했습니다" not in result
    if name == "Word":
        assert 'src="data:image/svg+xml;base64,' in result and 'alt="도형: 도형 글"' in result
    if name == "PowerPoint":
        assert '<figure class="slide-picture"><img src="data:image/' in result
    if name == "Excel":
        assert 'data-formula="=1+2"' in result
        assert '<figure class="chart"><img src="data:image/' in result and ': 도형 글"' in result
    if name == "Visio":
        assert "Office integration 한국어 → 연결 대상" in result
        assert '<figure><img src="data:image/' in result


SPREADSHEET = """<?xml version="1.0"?>
<?mso-application progid="Excel.Sheet"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:x="urn:schemas-microsoft-com:office:excel" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:html="http://www.w3.org/TR/REC-html40">
 <Styles>
  <Style ss:ID="Default" ss:Name="Normal"><Alignment ss:Vertical="Center"/>
   <Font ss:FontName="맑은 고딕" x:CharSet="129" ss:Size="11" ss:Color="#000000"/></Style>
  <Style ss:ID="s62"><Alignment ss:Horizontal="Center" ss:Vertical="Center"/>
   <Borders><Border ss:Position="Bottom" ss:LineStyle="Continuous" ss:Weight="2" ss:Color="#000000"/></Borders>
   <Font ss:FontName="맑은 고딕" ss:Size="11" ss:Color="#FFFFFF" ss:Bold="1"/><Interior ss:Color="#4472C4" ss:Pattern="Solid"/></Style>
  <Style ss:ID="s63"><NumberFormat ss:Format="#,##0.00"/></Style>
  <Style ss:ID="s64"><NumberFormat ss:Format="yyyy&quot;년&quot; m&quot;월&quot; d&quot;일&quot;"/></Style>
  <Style ss:ID="s65"><Font ss:Color="#FF0000"/><NumberFormat ss:Format="0.0%"/></Style>
  <Style ss:ID="s66"><Alignment ss:WrapText="1"/></Style>
 </Styles>
 <Worksheet ss:Name="매출">
  <Table ss:ExpandedColumnCount="3" ss:ExpandedRowCount="3" x:FullColumns="1" x:FullRows="1">
   <Column ss:Width="80.25"/><Column ss:Index="3" ss:Width="120"/>
   <Row ss:Height="20">
    <Cell ss:MergeAcross="1" ss:StyleID="s62"><Data ss:Type="String">매출 현황</Data></Cell>
    <Cell ss:HRef="https://wiki.test/a"><Data ss:Type="String">링크</Data></Cell>
   </Row>
   <Row>
    <Cell ss:StyleID="s63" ss:Formula="=SUM(R[1]C:R[2]C)"><Data ss:Type="Number">1234.5</Data></Cell>
    <Cell ss:StyleID="s64"><Data ss:Type="DateTime">2026-09-28T00:00:00.000</Data><Comment ss:Author="홍길동"><ss:Data
     xmlns="http://www.w3.org/TR/REC-html40"><B>홍길동:</B>&#10;확인 바랍니다</ss:Data></Comment></Cell>
    <Cell ss:StyleID="s65"><Data ss:Type="Number">0.256</Data></Cell>
   </Row>
   <Row ss:Hidden="1">
    <Cell ss:StyleID="s66"><ss:Data ss:Type="String" xmlns="http://www.w3.org/TR/REC-html40"><B>굵은</B> 글<Font
     html:Color="#FF0000">빨강</Font></ss:Data></Cell>
   </Row>
  </Table>
 </Worksheet>
</Workbook>"""  # noqa: E501


class SpreadsheetDispatch:
    """IDispatch of a fake Range: Value(11) is read through Invoke, as late-bound pywin32 must."""

    def __init__(self, xml, calls):
        self.xml, self.calls = xml, calls

    def GetIDsOfNames(self, name):
        self.calls.append(("name", name))
        return 6

    def Invoke(self, ident, lcid, flags, result, *args):
        self.calls.append(("invoke", ident, flags, args))
        return self.xml


def chart_fixture(title="분기 매출"):
    series = collection([NS(Name="2026", Values=(10.0, 12.5), XValues=("1분기", "2분기"))])
    return NS(HasTitle=bool(title), ChartTitle=NS(Text=title), ChartType=51, SeriesCollection=lambda: series)


def styled_workbook(calls):
    used = used_range(
        1,
        1,
        (("매출 현황", None, "링크"), (1234.5, 46293.0, 0.256), ("굵은 글빨강", None, None)),
        value=(
            ("매출 현황", None, "링크"),
            (1234.5, datetime(2026, 9, 28, tzinfo=timezone.utc), 0.256),
            ("굵은 글빨강", None, None),
        ),
        formula=(("매출 현황", "", "링크"), ("=SUM(A3:A4)", "46293", "0.256"), ("굵은 글빨강", "", "")),
    )
    used._oleobj_ = SpreadsheetDispatch(SPREADSHEET, calls)
    sheet = NS(
        Name="매출",
        Visible=-1,
        UsedRange=used,
        ChartObjects=lambda: collection([NS(Name="차트 1", Chart=chart_fixture())]),
        Shapes=collection(
            [
                NS(Type=13, Name="그림 2", AlternativeText="로고", TopLeftCell=NS(Address="$E$2")),
                NS(
                    Type=17,
                    Name="Text Box 3",
                    AlternativeText="",
                    TopLeftCell=NS(Address="$A$10"),
                    TextFrame2=NS(HasText=-1, TextRange=NS(Text="참고 사항")),
                ),
                NS(Type=3, Name="차트 1"),
            ]
        ),
        CommentsThreaded=collection(
            [
                NS(
                    Parent=NS(Address="$C$2"),
                    Author=NS(Name="김철수"),
                    Text=lambda: "검토했습니다",
                    Replies=collection([NS(Author=NS(Name="홍길동"), Text=lambda: "감사합니다")]),
                )
            ]
        ),
    )
    return NS(
        Worksheets=collection([sheet]),
        Charts=collection([NS(Name="추이", HasTitle=False, ChartType=4, SeriesCollection=lambda: collection([]))]),
    )


def test_excel_keeps_formatting_merged_cells_number_formats_comments_links_charts_and_shapes():
    calls = []
    body = excel_html(styled_workbook(calls))
    # Value(11) of the used range: XML Spreadsheet, in memory.
    assert calls == [("name", "Value"), ("invoke", 6, 2, (11,))]
    assert (
        '<table class="cells" style="font-family:&quot;맑은 고딕&quot;;font-size:11pt;color:#000000"><colgroup><col>'
        '<col style="width:80.25pt"><col><col style="width:120pt"></colgroup><tr><th></th><th>A</th><th>B</th>'
        "<th>C</th></tr>"
    ) in body
    assert (
        '<tr style="height:20pt"><th>1</th><td colspan="2" style="font-weight:bold;color:#FFFFFF;'
        "background-color:#4472C4;"
        'text-align:center;vertical-align:middle;border-bottom:1.5pt solid #000000">매출 현황</td>'
        '<td style="vertical-align:middle"><a href="https://wiki.test/a">링크</a></td></tr>'
    ) in body
    assert (
        '<td data-formula="=SUM(A3:A4)" title="=SUM(A3:A4)" style="text-align:right;vertical-align:middle">'
        "1,234.50</td>"
        '<td style="text-align:right;vertical-align:middle">2026년 9월 28일<span class="note-mark" title="메모">'
        "◥</span></td>"
        '<td style="color:#FF0000;text-align:right;vertical-align:middle">'
        '25.6%<span class="note-mark" title="메모">◥</span></td>'
    ) in body
    assert (
        '<tr class="hidden" title="숨긴 행"><th>3</th><td style="vertical-align:middle;white-space:pre-wrap">'
        '<strong>굵은</strong> 글<span style="color:#FF0000">빨강</span></td>'
    ) in body
    assert (
        "<li>B2 · 홍길동: 홍길동:<br>확인 바랍니다</li><li>C2 · 김철수: 검토했습니다</li>"
        "<li>C2 · 홍길동: 감사합니다</li>"
    ) in body
    assert "<figcaption>차트: 분기 매출 (세로 막대형)</figcaption>" in body and "<th>1분기</th><td>10</td>" in body
    assert '<li><span class="missing">[그림: 로고]</span> (E2)</li><li>참고 사항 (A10)</li>' in body
    assert "<h2>추이 (차트 시트)</h2>" in body
    text = markdown(body)
    assert "| 2 | 1,234.50 수식: `=SUM(A3:A4)` | 2026년 9월 28일 | 25.6% |" in text
    # A merged cell keeps its columns in Markdown with an empty cell.
    assert "| 1 | 매출 현황 | | [링크](https://wiki.test/a) |" in text and "| 1분기 | 10 |" in text


def test_excel_without_xml_spreadsheet_still_reads_values():
    calls = []
    workbook = styled_workbook(calls)
    workbook.Worksheets.Item(1).UsedRange._oleobj_ = SpreadsheetDispatch("<not xml", calls)
    body = excel_html(workbook)
    # Without formatting the values read as before: ISO dates, numbers as stored. The sheet says what is missing.
    assert body.startswith(
        f'<section class="sheet"><h2>매출</h2><p class="caption">{PLAIN_CELLS}</p><table class="cells">'
    )
    assert '<td data-formula="=SUM(A3:A4)" title="=SUM(A3:A4)" style="text-align:right">1234.5</td>' in body
    assert '<td style="text-align:right">2026-09-28</td>' in body and "colspan" not in body
    # Threaded comments are read from Excel itself and marked in their cell.
    assert (
        '0.256<span class="note-mark" title="메모">◥</span>' in body
        and "<figcaption>차트: 추이 (꺾은선형)</figcaption></figure>" in body
    )


def test_dates_of_a_workbook_in_the_1904_date_system():
    calls = []
    workbook = styled_workbook(calls)
    workbook.Date1904 = True
    used = workbook.Worksheets.Item(1).UsedRange
    # Excel hands over the serial of its date system: 2026-09-28 is day 44,831 counted from 1904.
    used.Value2 = tuple(tuple(44831.0 if value == 46293.0 else value for value in row) for row in used.Value2)
    assert "2026년 9월 28일" in excel_html(workbook)


@pytest.mark.parametrize(
    "value,code,expected",
    [
        (1234.5, "#,##0.00", "1,234.50"),
        (-1234.5, "#,##0;[Red](#,##0)", "(1,235)"),
        (0.0, '0;-0;"-"', "-"),
        (0.256, "0.0%", "25.6%"),
        (1234567.0, '#,##0,"천"', "1,235천"),
        (12345.678, "0.00E+00", "1.23E+04"),
        (1012345678.0, "000-0000-0000", "010-1234-5678"),
        (0.5, "#.##", ".5"),
        (-5.0, "0.00", "-5.00"),
        (46293.0, "yyyy-mm-dd", "2026-09-28"),
        (46293.5729166667, "[$-412]AM/PM h:mm", "오후 1:45"),
        (46293.0, "dddd", "월요일"),
        (46293.0, "[$-409]mmmm d, yyyy", "September 28, 2026"),
        (1.5, "[h]:mm", "36:00"),
        (0.3958333333, "h:mm:ss", "9:30:00"),
        (46293.0, "Short Date", "2026-09-28"),
        (0.123, "Percent", "12.30%"),
        ("abc", '@" 님"', "abc 님"),
        (9.999, "0.00E+00", "1.00E+01"),
        (0.000123, "0.00E+00", "1.23E-04"),
        (12345.0, "##0.0E+0", "12.3E+3"),
    ],
)
def test_number_formats_read_as_excel_shows_them(value, code, expected):
    assert format_value(value, code)[0] == expected


@pytest.mark.parametrize(
    "value,code",
    [
        (5.0, "General"),
        (2.5, "# ?/?"),
        (True, "0.00"),
        ("text", "0.00"),
        (2007, "0"),
        (5.0, "E-[Red]#[Red]"),
        (5.0, "[>=1.2.3]0;0"),
    ],
)
def test_number_formats_it_does_not_read_are_left_to_the_general_format(value, code):
    assert format_value(value, code) is None


@pytest.mark.parametrize(
    "value,code,date1904,expected",
    [
        # Excel counts 1900 as a leap year: day 1 is 1900-01-01, day 60 the 29 February that never was, day 0
        # "1900-01-00", and the weekdays before March 1900 are Excel's (1900-01-01 a Sunday).
        (1.0, "yyyy-mm-dd", False, "1900-01-01"),
        (59.0, "yyyy-mm-dd", False, "1900-02-28"),
        (60.0, "yyyy-mm-dd dddd", False, "1900-02-29 수요일"),
        (61.0, "yyyy-mm-dd dddd", False, "1900-03-01 목요일"),
        (0.5, "yyyy-mm-dd h:mm", False, "1900-01-00 12:00"),
        (1.0, "[$-409]dddd", False, "Sunday"),
        (2.5, "[h]:mm", False, "60:00"),
        # A workbook of the 1904 date system counts from 1904-01-01, four years and a day later.
        (44831.0, "yyyy-mm-dd", True, "2026-09-28"),
        (0.0, "yyyy-mm-dd", True, "1904-01-01"),
        (60.0, "yyyy-mm-dd", True, "1904-03-01"),
        (1.5, "[h]:mm", True, "36:00"),
    ],
)
def test_dates_read_as_the_date_system_of_the_workbook_shows_them(value, code, date1904, expected):
    assert format_value(value, code, date1904)[0] == expected


def test_any_number_format_code_is_read_or_left_to_the_general_format():
    """Format codes come from the file: whatever they hold, a cell is never what stops the import."""
    import random

    generator = random.Random(1904)
    pieces = [
        "0",
        "#",
        "?",
        ".",
        ",",
        "%",
        "E+",
        "E-",
        "e-",
        "/",
        "@",
        ";",
        "[Red]",
        "[>=10]",
        "[<-1.5]",
        "[=1.2.3]",
        "[$-412]",
        "[$₩-412]",
        "[h]",
        "[mm]",
        "[ss]",
        "yyyy",
        "mmmm",
        "m",
        "d",
        "dddd",
        "aaa",
        "h",
        "s",
        ".00",
        "AM/PM",
        "A/P",
        "General",
        '"원"',
        "\\-",
        "_)",
        "*-",
        " ",
        "(",
        ")",
        "[",
        "]",
        '"',
    ]
    for _ in range(3000):
        code = "".join(generator.choice(pieces) for _ in range(generator.randrange(1, 9)))
        for value in (0.0, 1.0, -2.5, 60.0, 1234567.891, 1e-9, 1e300, "글"):
            found = format_value(value, code, generator.random() < 0.5)
            assert found is None or (isinstance(found[0], str) and found[1] in (None, "#FF0000")), code


def rich_text(*paragraphs):
    """A PowerPoint TextRange with Paragraphs() and Runs(), each paragraph (level, alignment, bullet, runs)."""
    items = []
    for level, alignment, bullet, runs in paragraphs:
        objects = [
            NS(
                Text=text,
                Font=NS(
                    **{
                        "Name": "맑은 고딕",
                        "Size": 18.0,
                        "Bold": 0,
                        "Italic": 0,
                        "Underline": 0,
                        "Color": NS(RGB=0),
                        "Superscript": 0,
                        "Subscript": 0,
                        **font,
                    }
                ),
            )
            for text, font in runs
        ]
        items.append(
            NS(
                Text="".join(text for text, _ in runs),
                IndentLevel=level,
                ParagraphFormat=NS(Alignment=alignment, Bullet=bullet),
                Runs=lambda index=None, objects=objects: collection(objects) if index is None else objects[index - 1],
            )
        )
    return NS(
        Text="\r".join(item.Text for item in items),
        Paragraphs=lambda index=None: collection(items) if index is None else items[index - 1],
    )


def test_powerpoint_keeps_run_formatting_bullets_fills_charts_pictures_comments_and_hidden_slides():
    text = NS(
        Type=17,
        HasTable=False,
        HasChart=0,
        HasSmartArt=False,
        HasTextFrame=True,
        TextFrame=NS(
            HasText=True,
            TextRange=rich_text(
                (
                    1,
                    1,
                    NS(Visible=-1, Type=1, Character=0x2022),
                    [("굵게 ", {"Bold": -1, "Size": 24.0}), ("빨강", {"Color": NS(RGB=0x0000FF)})],
                ),
                (2, 2, NS(Visible=-1, Type=2, Number=3, Style=7), [("가운데", {})]),
                (1, 1, NS(Visible=-1, Type=1, Character=0xF0D8, Font=NS(Name="Wingdings")), [("화살표 글머리", {})]),
                (1, 1, NS(Visible=0), [("H", {}), ("2", {"Subscript": -1}), ("O", {})]),
            ),
        ),
    )
    filled = NS(
        Shape=NS(Fill=NS(Visible=-1, ForeColor=NS(RGB=0xCCF2FF)), TextFrame=NS(HasText=True, TextRange=NS(Text="채움")))
    )
    table = NS(
        Type=19,
        HasTable=True,
        Table=NS(Rows=NS(Count=1), Columns=NS(Count=1), FirstRow=False, Cell=lambda r, c: filled),
    )
    chart = NS(Type=3, HasTable=False, HasChart=-1, Chart=chart_fixture("점유율"), Name="차트 2")
    picture = NS(Type=13, HasTable=False, HasChart=0, HasSmartArt=False, AlternativeText="제품 사진", Name="그림 3")
    slide = NS(
        Shapes=collection([placeholder(1, "결과"), text, table, chart, picture]),
        NotesPage=NS(Shapes=collection([])),
        SlideShowTransition=NS(Hidden=-1),
        Comments=collection([NS(Author="홍길동", Text="수치 확인")]),
        Hyperlinks=collection([NS(Address="https://wiki.test/r", TextToDisplay="보고서", SubAddress="")]),
    )
    body = powerpoint_html(NS(Slides=collection([slide])))
    assert '<section class="slide hidden"><h2>슬라이드 1 · 결과 (숨긴 슬라이드)</h2>' in body
    assert (
        '<p><span class="bullet">• </span><span style="font-family:&quot;맑은 고딕&quot;;font-size:24pt;'
        'font-weight:bold">굵게 </span>'
        '<span style="font-family:&quot;맑은 고딕&quot;;font-size:18pt;color:#FF0000">빨강</span></p>'
    ) in body
    assert '<p style="margin-left:18pt;text-align:center"><span class="bullet">III. </span>' in body
    assert '<span class="bullet">➢ </span>' in body
    assert "<sub><span" not in body and "<sub>2</sub>" in body
    assert '<td style="background-color:#FFF2CC"><p>채움</p></td>' in body
    assert "<figcaption>차트: 점유율 (세로 막대형)</figcaption>" in body
    assert '<span class="missing">[그림: 제품 사진]</span>' in body
    assert "<li>홍길동: 수치 확인</li>" in body and '<li><a href="https://wiki.test/r">보고서</a></li>' in body
    result = markdown(body, "powerpoint")
    assert "• 굵게 빨강" in result and "III. 가운데" in result and "➢ 화살표 글머리" in result


def test_properties_with_arguments_go_through_invoke_and_wait_while_office_is_busy(monkeypatch):
    monkeypatch.setattr("agent.office_session.time.sleep", lambda seconds: None)
    attempts = []

    names = []

    class Dispatch:
        def GetIDsOfNames(self, name):
            # Asking for the name is a call Excel rejects while busy too.
            names.append(name)
            if len(names) < 2:
                raise Busy()
            return {"Value": 6}[name]

        def Invoke(self, *args):
            attempts.append(args)
            if len(attempts) < 2:
                raise Busy()
            return "<Workbook/>"

    session = OfficeSession("Excel", PROGIDS["Excel"])
    used = Patient(NS(_oleobj_=Dispatch()), session)
    assert property_with(used, "Value", 11) == "<Workbook/>"
    assert attempts == [(6, 0, 2, True, 11)] * 2 and names == ["Value"] * 2
