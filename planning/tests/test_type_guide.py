import uuid
from datetime import date, timedelta

from django.test import TestCase
from django.utils.html import escape

from planning.models import GameType
from planning.services import periods
from planning.services.actors import Actor
from planning.tests import web_client


@web_client
class TypeGuideTests(TestCase):
    def setUp(self):
        day = date.today() + timedelta(days=7)
        day += timedelta(days=(5 - day.weekday()) % 7)
        self.period = periods.create_period(
            actor=Actor.bureau(),
            label="Guide des types",
            start_date=day,
            end_date=day,
            creation_key=uuid.uuid4(),
        )
        self.url = f"/?period={self.period.pk}&filtre=toutes"

    def test_seeded_definitions_in_single_closed_guide(self):
        response = self.client.get(self.url)
        self.assertContains(response, '<details class="type-guide" id="type-guide">', count=1)
        self.assertNotContains(response, '<details class="type-guide" id="type-guide" open')
        self.assertContains(response, "Parties ouvertes à tous", count=1)
        self.assertContains(response, "Parties plus scénarisées et élaborées", count=1)

    def test_custom_name_and_description_are_escaped(self):
        game_type = GameType.objects.create(
            code="custom",
            name="<img src=x onerror=alert(1)>",
            description="<script>alert('x')</script>\nDeuxieme ligne.",
        )
        response = self.client.get(self.url)
        self.assertIn(game_type, list(response.context["described_game_types"]))
        self.assertContains(response, escape(game_type.name))
        self.assertContains(response, escape(game_type.description))
        self.assertNotContains(response, game_type.name)
        self.assertNotContains(response, "<script>alert('x')</script>")

    def test_blank_descriptions_omitted_and_empty_guide_hidden(self):
        blank = GameType.objects.create(code="blank", name="Sans description")
        response = self.client.get(self.url)
        self.assertNotIn(blank, list(response.context["described_game_types"]))
        GameType.objects.update(description="")
        response = self.client.get(self.url)
        self.assertNotContains(response, 'id="type-guide"')

    def test_inactive_type_only_included_when_visible(self):
        used = GameType.objects.create(
            code="old-used",
            name="Ancien format",
            description="Ancienne definition.",
            is_active=False,
        )
        unused = GameType.objects.create(
            code="old-unused",
            name="Format inutilise",
            description="Definition non utilisee.",
            is_active=False,
        )
        event = self.period.events.get()
        event.game_type = used
        event.type_label = used.name
        event.save(update_fields=["game_type", "type_label"])
        response = self.client.get(self.url)
        definitions = list(response.context["described_game_types"])
        self.assertIn(used, definitions)
        self.assertNotIn(unused, definitions)
        self.assertContains(response, used.description)
        event.is_hidden = True
        event.save(update_fields=["is_hidden"])
        response = self.client.get(self.url)
        self.assertNotIn(used, list(response.context["described_game_types"]))
