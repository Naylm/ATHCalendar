from io import BytesIO

import xlsxwriter
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from . import reports, selectors
from .errors import ServiceError
from .models import Period
from .permissions import bureau_page
from .services.periods import get_period
from .services.validators import parse_uuid


def _period_or_404(period_id):
    try:
        return get_period(period_id)
    except ServiceError:
        raise Http404("Période introuvable.") from None


@never_cache
@require_GET
def recap(request):
    period_id = request.GET.get("period")
    if period_id:
        try:
            period_id = parse_uuid(period_id, "Période")
        except ServiceError:
            raise Http404("Période introuvable.") from None
        period = _period_or_404(period_id)
    else:
        period = selectors.default_period()
    return render(
        request,
        "planning/recap.html",
        {
            "period": period,
            "periods": Period.objects.order_by("start_date", "created_at"),
            "people": reports.public_recap(period) if period else (),
        },
    )


def _xlsx_sheet(workbook, name, title, subtitle, headers, rows, widths):
    sheet = workbook.add_worksheet(name)
    heading = workbook.add_format(
        {"bold": True, "font_size": 18, "font_color": "#394331", "text_wrap": True}
    )
    note = workbook.add_format({"font_color": "#666666", "italic": True})
    text = workbook.add_format({"text_wrap": True, "valign": "top"})
    date_format = workbook.add_format({"num_format": "dd/mm/yyyy", "valign": "top"})
    sheet.merge_range(0, 0, 0, len(headers) - 1, title, heading)
    sheet.set_row(0, 32)
    sheet.merge_range(1, 0, 1, len(headers) - 1, subtitle, note)
    for column, width in enumerate(widths):
        sheet.set_column(column, column, width, text)
    if rows:
        sheet.add_table(
            3,
            0,
            3 + len(rows),
            len(headers) - 1,
            {
                "columns": [{"header": header} for header in headers],
                "style": "Table Style Medium 4",
            },
        )
    else:
        sheet.write_row(3, 0, headers, workbook.add_format({"bold": True}))
        sheet.write_string(4, 0, "Aucune donnée pour cette période.")
    for row_index, row in enumerate(rows, start=4):
        for column, value in enumerate(row):
            if hasattr(value, "isoformat"):
                sheet.write_datetime(row_index, column, value, date_format)
            elif isinstance(value, int):
                sheet.write_number(row_index, column, value)
            else:
                sheet.write_string(row_index, column, str(value), text)
    sheet.freeze_panes(4, 1)
    sheet.repeat_rows(3)
    sheet.set_landscape()
    sheet.fit_to_pages(1, 0)
    sheet.print_area(0, 0, max(4, 3 + len(rows)), len(headers) - 1)
    sheet.set_footer("&LATH Calendar&RPage &P / &N")


@never_cache
@bureau_page
@require_GET
def export_xlsx(request, period_id):
    table = reports.period_table(_period_or_404(period_id))
    output = BytesIO()
    with xlsxwriter.Workbook(
        output,
        {"in_memory": True, "strings_to_formulas": False, "strings_to_urls": False},
    ) as workbook:
        subtitle = f"Du {table.start_date:%d/%m/%Y} au {table.end_date:%d/%m/%Y}" + (
            " — Période archivée" if table.archived else ""
        )
        planning = []
        for row in table.planning:
            names = ", ".join(row.organizer_names)
            if len(names) > 32000:
                names = "Liste complète dans l’onglet Inscriptions."
            planning.append(
                [
                    row.date,
                    row.type_label,
                    row.title,
                    row.status,
                    row.availability,
                    "Masquée" if row.hidden else "Visible",
                    row.minimum,
                    row.capacity if row.capacity is not None else "Illimitée",
                    row.active_count,
                    names,
                    row.public_note,
                ]
            )
        _xlsx_sheet(
            workbook,
            "Planning",
            table.label,
            subtitle + " — Toutes les dates, y compris les dates masquées.",
            [
                "Date",
                "Type",
                "Titre",
                "État",
                "Inscriptions",
                "Visibilité",
                "Minimum",
                "Capacité",
                "Inscrits actifs",
                "Organisateurs",
                "Note publique",
            ],
            planning,
            [14, 19, 24, 13, 30, 13, 10, 12, 15, 42, 48],
        )
        _xlsx_sheet(
            workbook,
            "Inscriptions",
            table.label,
            "Historique des inscriptions : inscrits et retirés, avec les noms enregistrés.",
            ["Date", "Type", "Visibilité", "Pseudo enregistré", "Inscription", "Note"],
            [
                [
                    row.date,
                    row.type_label,
                    "Masquée" if row.hidden else "Visible",
                    row.organizer_name,
                    row.state,
                    row.note,
                ]
                for row in table.registrations
            ],
            [14, 19, 13, 28, 16, 60],
        )
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="ath-planning-{table.period_id}.xlsx"'
    return response


@never_cache
@bureau_page
@require_GET
def printable(request, period_id):
    return render(
        request,
        "bureau/period_print.html",
        {"table": reports.period_table(_period_or_404(period_id))},
    )
