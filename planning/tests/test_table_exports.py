import uuid
from datetime import date
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.utils.html import strip_tags

from planning.models import Event, Organizer, Period, Registration
from planning.services import types
from planning.services.actors import Actor
from planning.tests import web_client

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@web_client
class TableExportTests(TestCase):
    def setUp(self):
        self.period = Period.objects.create(
            creation_key=uuid.uuid4(),
            label="=Planning",
            start_date=date(2030, 1, 1),
            end_date=date(2030, 1, 31),
        )
        self.event = Event.objects.create(period=self.period, date=date(2030, 1, 5))
        self.organizer = Organizer.objects.create(name="=Alice", normalized_key="=alice")
        Registration.objects.create(
            event=self.event,
            organizer=self.organizer,
            organizer_name="=Alice",
            note="=1+1",
        )
        hidden = Event.objects.create(period=self.period, date=date(2030, 1, 6), is_hidden=True)
        hidden_person = Organizer.objects.create(name="Secret masqué", normalized_key="secret")
        Registration.objects.create(
            event=hidden, organizer=hidden_person, organizer_name=hidden_person.name
        )
        group, _ = Group.objects.get_or_create(name="Bureau")
        user = User.objects.create_user(username="bureau")
        user.groups.add(group)
        self.bureau = Client()
        self.bureau.force_login(user)

    def test_exports_are_bureau_only_and_public_recap_is_removed(self):
        root = f"/bureau/exports/periode/{self.period.pk}"
        for path in (root + ".xlsx", root + "/imprimer/"):
            self.assertRedirects(
                Client().get(path), "/bureau/connexion/", fetch_redirect_response=False
            )
        self.assertEqual(Client().get(f"/recapitulatif/?period={self.period.pk}").status_code, 404)
        self.assertEqual(Client().get("/recapitulatif/").status_code, 404)
        index = Client().get("/")
        self.assertNotContains(index, "Par personne")
        self.assertNotContains(index, "/recapitulatif/")

    def test_xlsx_is_a_real_formatted_workbook_without_user_formulas(self):
        response = self.bureau.get(f"/bureau/exports/periode/{self.period.pk}.xlsx")
        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml", response["Content-Type"])
        self.assertIn("no-store", response["Cache-Control"])
        with ZipFile(BytesIO(response.content)) as archive:
            sheets = [
                name
                for name in archive.namelist()
                if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
            ]
            self.assertEqual(len(sheets), 2)
            self.assertIn("xl/tables/table1.xml", archive.namelist())
            self.assertIn("xl/tables/table2.xml", archive.namelist())
            for name in sheets:
                root = ElementTree.fromstring(archive.read(name))
                self.assertEqual(root.findall(".//m:f", NS), [])
                self.assertEqual(root.find(".//m:pane", NS).get("state"), "frozen")
                self.assertEqual(root.findall(".//m:hyperlink", NS), [])
            strings = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            values = ["".join(item.itertext()) for item in strings]
            for value in ("=Planning", "=Alice", "=1+1", "Secret masqué", "Masquée"):
                self.assertIn(value, values)
            styles = archive.read("xl/styles.xml").decode()
            self.assertIn("dd/mm/yyyy", styles)

    def test_printable_table_is_escaped_and_includes_visibility(self):
        self.event.public_note = "<script>alert(1)</script>"
        self.event.save(update_fields=["public_note"])
        response = self.bureau.get(f"/bureau/exports/periode/{self.period.pk}/imprimer/")
        self.assertContains(response, "<table")
        self.assertContains(response, "Masquée")
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")

    def test_empty_period_has_valid_workbook(self):
        empty = Period.objects.create(
            creation_key=uuid.uuid4(),
            label="Vide",
            start_date=date(2031, 1, 1),
            end_date=date(2031, 1, 31),
        )
        response = self.bureau.get(f"/bureau/exports/periode/{empty.pk}.xlsx")
        with ZipFile(BytesIO(response.content)) as archive:
            self.assertIsNone(archive.testzip())

    def test_color_picker_displays_swatch_and_names_not_hex_text(self):
        game_type = types.create_type(
            actor=Actor.bureau(), name="Couleur test", code="couleur-test", color="#60766b"
        )
        response = self.bureau.get("/bureau/types/")
        visible = strip_tags(response.content.decode())
        self.assertIn("Sauge", visible)
        self.assertNotIn("#60766b", visible)
        self.assertContains(response, "color-swatch type-c4")
        self.event.game_type = game_type
        self.event.type_label = game_type.name
        self.event.save(update_fields=["game_type", "type_label"])
        response = Client().get(f"/?period={self.period.pk}")
        self.assertContains(response, 'class="event-type-select type-c4"')
