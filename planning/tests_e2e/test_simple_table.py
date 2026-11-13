import re
import unittest
from contextlib import contextmanager

from playwright.sync_api import expect

from planning.tests_e2e.test_ui import (
    SCREENSHOT_DIR,
    LiveServer,
    TrackedPage,
    browser_tests,
    check_no_horizontal_overflow,
    seed_period_script,
)

VIEWPORTS = ((320, 740), (390, 844), (1440, 900))


class SimpleTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if browser_tests() is None:
            raise unittest.SkipTest("playwright non disponible")

    @contextmanager
    def _server(self):
        with LiveServer() as server:
            server.seed(seed_period_script())
            server.seed(
                "from planning.services import periods\n"
                "from planning.services.actors import Actor\n"
                "from planning.models import Period\n"
                "p = Period.objects.order_by('start_date').first()\n"
                "periods.set_active(p.pk, actor=Actor.bureau())\n"
            )
            yield server

    def _page(self, pw, server, width, height):
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": width, "height": height})
        tracked = TrackedPage(context.new_page())
        tracked.page.goto(server.url + "/")
        tracked.page.wait_for_selector(".name-registration-form", timeout=8000)
        return browser, tracked

    def _seed_names_and_type(self, server, event_id, names=("Test", "Test2", "Test3")):
        names_src = repr(tuple(names))
        server.seed(
            "from planning.services import organizers, registrations, events\n"
            "from planning.services.actors import Actor\n"
            "from planning.models import GameType\n"
            f"for n in {names_src}:\n"
            "    o = organizers.get_or_create_organizer(n)\n"
            f"    registrations.set_registration('{event_id}', o.pk,"
            " 'active', actor=Actor.public(o))\n"
            f"events.update_event('{event_id}', actor=Actor.bureau(),"
            " game_type_id=str(GameType.objects.get(name='Flex').pk))\n"
        )

    def _assert_grid_masked(self, page):
        styles = page.evaluate(
            """() => { const b = getComputedStyle(document.body);
                const c = getComputedStyle(document.querySelector('.content'));
                return { bodyImg: b.backgroundImage, bodySize: b.backgroundSize,
                    bodyColor: b.backgroundColor, contentImg: c.backgroundImage,
                    contentColor: c.backgroundColor }; }"""
        )
        assert "linear-gradient" in styles["bodyImg"], styles
        assert styles["bodySize"].startswith("72px 72px"), styles
        assert styles["contentImg"] == "none", styles
        assert styles["contentColor"] == styles["bodyColor"], styles
        assert styles["contentColor"] != "rgba(0, 0, 0, 0)", styles

    def _column_xs(self, page):
        return page.evaluate(
            """[...document.querySelectorAll('.planning-columns > span')]
            .map(el => el.getBoundingClientRect().x)"""
        )

    def test_spreadsheet_layout_viewports(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = server.query(
                "from planning.models import Event;print(Event.objects.order_by('date')[0].pk)"
            )
            cancelled_id = server.query(
                "from planning.models import Event;print(Event.objects.order_by('date')[1].pk)"
            )
            self._seed_names_and_type(server, event_id)
            server.seed(
                "from planning.services import events\n"
                "from planning.services.actors import Actor\n"
                f"e = events.cancel_event('{cancelled_id}', actor=Actor.bureau())\n"
            )
            for width, height in VIEWPORTS:
                browser, tracked = self._page(pw, server, width, height)
                page = tracked.page
                check_no_horizontal_overflow(page)
                header = page.locator(".planning-columns")
                row = page.locator(f"#date-{event_id}")
                date_cell = row.locator(".event-date-cell").bounding_box()
                organizers = row.locator(".event-organizers").bounding_box()
                type_control = row.locator(".event-type-select").bounding_box()
                if width >= 720:
                    assert header.is_visible()
                    labels = page.evaluate(
                        "[...document.querySelectorAll('.planning-columns > span')]"
                        ".map(el => el.textContent.trim())"
                    )
                    assert labels == ["Date", "Organisateurs", "Type"], labels
                    cols = self._column_xs(page)
                    assert len(cols) == 3
                    for box, x in zip((date_cell, organizers, type_control), cols, strict=False):
                        assert abs(box["x"] - x) <= 2, (width, box["x"], x)
                    cancelled_row = page.locator(f"#date-{cancelled_id}")
                    assert cancelled_row.locator(
                        ".event-lock-status", has_text="Annulée"
                    ).is_visible()
                    cancelled_tag = cancelled_row.locator(".event-when > .tag")
                    assert cancelled_tag.is_visible()
                    tag_x = cancelled_tag.bounding_box()["x"]
                    assert abs(tag_x - cols[2]) <= 2, (width, tag_x, cols[2])
                else:
                    assert not header.is_visible()
                    row_box = row.bounding_box()
                    assert abs(organizers["x"] - row_box["x"]) <= 2, (
                        width,
                        organizers,
                        row_box,
                    )
                    assert organizers["width"] >= row_box["width"] - 14, (
                        width,
                        organizers,
                        row_box,
                    )
                    line1_bottom = max(
                        date_cell["y"] + date_cell["height"],
                        type_control["y"] + type_control["height"],
                    )
                    assert organizers["y"] >= line1_bottom - 1, (
                        width,
                        organizers,
                        date_cell,
                        type_control,
                    )
                    empty_row = page.locator(f"#date-{cancelled_id}")
                    empty_org = empty_row.locator(".event-organizers").bounding_box()
                    empty_tag = empty_row.locator(".event-when > .tag").bounding_box()
                    empty_date = empty_row.locator(".event-date-cell").bounding_box()
                    assert empty_org["x"] > empty_date["x"], (
                        width,
                        empty_org,
                        empty_date,
                    )
                    assert empty_tag["x"] > empty_org["x"], (
                        width,
                        empty_tag,
                        empty_org,
                    )
                style = row.evaluate(
                    """el => { const s = getComputedStyle(el); return {
                        bg: s.backgroundColor, radius: s.borderRadius,
                        left: s.borderLeftWidth, top: s.borderTopWidth,
                        bottom: s.borderBottomWidth }; }"""
                )
                assert style["bg"] in ("rgba(0, 0, 0, 0)", "transparent"), style
                assert style["radius"] == "0px", style
                assert style["left"] == "0px" and style["top"] == "0px", style
                assert style["bottom"] == "1px", style
                font = row.locator(".organizer-name").first.evaluate(
                    "el => parseFloat(getComputedStyle(el).fontSize)"
                )
                assert font >= 15, (width, font)
                input_el = row.locator(".name-registration-form input[name=name]")
                assert input_el.get_attribute("placeholder") == "Nom"
                button = row.locator(".name-registration-form button[type=submit]")
                assert button.get_attribute("aria-label") == "Ajouter votre nom"
                if width < 960:
                    for href in ("/", "/bureau/"):
                        link = page.locator(f".top-nav a[href='{href}']")
                        assert link.is_visible()
                        assert link.bounding_box()["height"] >= 44
                page.screenshot(
                    path=str(SCREENSHOT_DIR / f"spreadsheet-simple-{width}.png"),
                    full_page=False,
                )
                tracked.assert_clean()
                browser.close()

    def _weekend_pair(self, server):
        return server.query(
            "from planning.models import Event\n"
            "events = list(Event.objects.order_by('date'))\n"
            "sat = next(e for e in events if e.date.weekday() == 5"
            " and any(o.date.weekday() == 6 and (o.date - e.date).days == 1"
            " for o in events))\n"
            "sun = next(e for e in events"
            " if (e.date - sat.date).days == 1 and e.date.weekday() == 6)\n"
            "nxt = next(e for e in events if e.date > sun.date)\n"
            "nxt_sun = next(e for e in events if e.date > nxt.date)\n"
            "print(sat.pk, sun.pk, nxt.pk, nxt_sun.pk)\n"
        ).split()

    def test_weekend_separators_viewports(self):
        with self._server() as server, browser_tests()() as pw:
            sat_id, sun_id, next_sat_id, _ = self._weekend_pair(server)
            for width, height in VIEWPORTS:
                browser, tracked = self._page(pw, server, width, height)
                page = tracked.page
                check_no_horizontal_overflow(page)
                sat = page.locator(f"#date-{sat_id}")
                sun = page.locator(f"#date-{sun_id}")
                nxt = page.locator(f"#date-{next_sat_id}")
                sat_box = sat.bounding_box()
                sun_box = sun.bounding_box()
                nxt_box = nxt.bounding_box()
                pair_gap = sun_box["y"] - (sat_box["y"] + sat_box["height"])
                assert pair_gap <= 2, (width, pair_gap)
                weekend_gap = nxt_box["y"] - (sun_box["y"] + sun_box["height"])
                assert weekend_gap >= 12, (width, weekend_gap)
                style = nxt.evaluate(
                    """el => { const s = getComputedStyle(el); return {
                        w: parseFloat(s.borderTopWidth), c: s.borderTopColor }; }"""
                )
                assert style["w"] >= 2, (width, style)
                assert style["c"] not in ("rgba(0, 0, 0, 0)", "transparent"), style
                assert "weekend-start" in (nxt.get_attribute("class") or "")
                assert "weekend-start" not in (sun.get_attribute("class") or "")
                page.screenshot(
                    path=str(SCREENSHOT_DIR / f"weekend-separators-{width}.png"),
                    full_page=False,
                )
                tracked.assert_clean()
                browser.close()

    def test_weekend_separator_live_recompute(self):
        with self._server() as server, browser_tests()() as pw:
            _, _, sat_id, sun_id = self._weekend_pair(server)
            browser, tracked = self._page(pw, server, 390, 844)
            page = tracked.page
            row = page.locator(f"#date-{sun_id}")
            row.locator(".name-registration-form input[name=name]").fill("Brouillon")
            server.seed(
                "from planning.services import events\n"
                "from planning.services.actors import Actor\n"
                "from planning.models import Event\n"
                f"e = Event.objects.get(pk='{sat_id}')\n"
                f"events.set_event_hidden('{sat_id}', actor=Actor.bureau(),"
                " is_hidden=True, expected_version=e.version)\n"
            )
            expect(page.locator(f"#date-{sun_id}")).to_have_class(
                re.compile(r"weekend-start"), timeout=8000
            )
            assert (
                page.locator(
                    f"#date-{sun_id} .name-registration-form input[name=name]"
                ).input_value()
                == "Brouillon"
            )
            server.seed(
                "from planning.services import events\n"
                "from planning.services.actors import Actor\n"
                "from planning.models import Event\n"
                f"e = Event.objects.get(pk='{sat_id}')\n"
                f"events.set_event_hidden('{sat_id}', actor=Actor.bureau(),"
                " is_hidden=False, expected_version=e.version)\n"
            )
            expect(page.locator(f"#date-{sat_id}")).to_have_class(
                re.compile(r"weekend-start"), timeout=8000
            )
            expect(page.locator(f"#date-{sun_id}")).not_to_have_class(
                re.compile(r"weekend-start"), timeout=8000
            )
            assert (
                page.locator(
                    f"#date-{sun_id} .name-registration-form input[name=name]"
                ).input_value()
                == "Brouillon"
            )
            tracked.assert_clean()
            browser.close()

    def test_mobile_names_populated_layout(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = server.query(
                "from planning.models import Event;print(Event.objects.order_by('date')[0].pk)"
            )
            self._seed_names_and_type(server, event_id, names=("Test", "BONNIN Melvin"))
            for width, height in ((320, 740), (390, 844), (430, 932)):
                browser = pw.chromium.launch()
                context = browser.new_context(
                    viewport={"width": width, "height": height}, has_touch=True
                )
                tracked = TrackedPage(context.new_page())
                page = tracked.page
                page.goto(server.url + "/")
                page.wait_for_selector(".organizer-name", timeout=8000)
                self._assert_grid_masked(page)
                check_no_horizontal_overflow(page)
                row = page.locator(f"#date-{event_id}")
                names = row.locator(".organizer-name")
                assert names.count() == 2
                boxes = [names.nth(i).bounding_box() for i in range(2)]
                assert abs(boxes[0]["y"] - boxes[1]["y"]) <= 1, (width, boxes)
                row_box = row.bounding_box()
                for box in boxes:
                    assert box["x"] >= row_box["x"], (width, box)
                    assert box["x"] + box["width"] <= row_box["x"] + row_box["width"], (
                        width,
                        box,
                    )
                expect(row.locator(".event-names")).to_contain_text("BONNIN Melvin")
                for i in range(2):
                    btn = names.nth(i).locator(".remove-registration").bounding_box()
                    assert btn["width"] >= 44 and btn["height"] >= 44, (width, btn)
                organizers = row.locator(".event-organizers").bounding_box()
                date_cell = row.locator(".event-date-cell").bounding_box()
                type_control = row.locator(".event-type-select").bounding_box()
                assert (
                    organizers["y"]
                    >= max(
                        date_cell["y"] + date_cell["height"],
                        type_control["y"] + type_control["height"],
                    )
                    - 1
                ), (width, organizers, date_cell, type_control)
                page.screenshot(
                    path=str(SCREENSHOT_DIR / f"mobile-names-{width}.png"),
                    full_page=False,
                )
                tracked.assert_clean()
                browser.close()
            browser, tracked = self._page(pw, server, 1440, 900)
            self._assert_grid_masked(tracked.page)
            tracked.page.screenshot(
                path=str(SCREENSHOT_DIR / "desktop-names-1440.png"), full_page=False
            )
            tracked.assert_clean()
            browser.close()

    def test_live_add_remove_toggles_mobile_layout(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = server.query(
                "from planning.models import Event;print(Event.objects.order_by('date')[0].pk)"
            )
            browser = pw.chromium.launch()
            context = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
            tracked = TrackedPage(context.new_page())
            page = tracked.page
            page.goto(server.url + "/")
            page.wait_for_selector(".name-registration-form", timeout=8000)
            row = page.locator(f"#date-{event_id}")
            input_el = row.locator(".name-registration-form input[name=name]")
            date_cell = row.locator(".event-date-cell").bounding_box()
            org_box = row.locator(".event-organizers").bounding_box()
            assert org_box["y"] < date_cell["y"] + date_cell["height"], (
                org_box,
                date_cell,
            )
            input_el.fill("Solo")
            input_el.press("Enter")
            expect(row.locator(".organizer-name")).to_have_count(1, timeout=8000)
            org_box = row.locator(".event-organizers").bounding_box()
            date_cell = row.locator(".event-date-cell").bounding_box()
            type_box = row.locator(".event-type-select").bounding_box()
            assert (
                org_box["y"]
                >= max(
                    date_cell["y"] + date_cell["height"],
                    type_box["y"] + type_box["height"],
                )
                - 1
            ), (org_box, date_cell, type_box)
            input_el.fill("Brouillon")
            page.once("dialog", lambda d: d.accept())
            row.locator(".remove-registration").first.click()
            expect(row.locator(".organizer-name")).to_have_count(0, timeout=8000)
            org_box = row.locator(".event-organizers").bounding_box()
            date_cell = row.locator(".event-date-cell").bounding_box()
            assert org_box["y"] < date_cell["y"] + date_cell["height"], (
                org_box,
                date_cell,
            )
            assert input_el.input_value() == "Brouillon"
            tracked.assert_clean()
            browser.close()

    def test_long_content_no_overflow(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = server.query(
                "from planning.models import Event;print(Event.objects.order_by('date')[0].pk)"
            )
            long_name = "Prénom-Très-Long-Nom-De-Famille-Quarante"
            self._seed_names_and_type(server, event_id, names=(long_name,))
            server.seed(
                "from planning.models import GameType\n"
                "t = GameType.objects.create(name='TypeDePartieAuNomTresLongPourTester',"
                " code='long-type-test', color='#6b7c4a', is_active=True)\n"
                "from planning.services import events\n"
                "from planning.services.actors import Actor\n"
                f"events.update_event('{event_id}', actor=Actor.bureau(),"
                " game_type_id=str(t.pk))\n"
            )
            for width, height in ((320, 740), (1440, 900)):
                browser, tracked = self._page(pw, server, width, height)
                page = tracked.page
                check_no_horizontal_overflow(page)
                row = page.locator(f"#date-{event_id}")
                expect(row.locator(".event-names")).to_contain_text(long_name[:20])
                page.screenshot(
                    path=str(SCREENSHOT_DIR / f"spreadsheet-long-{width}.png"),
                    full_page=False,
                )
                tracked.assert_clean()
                browser.close()


if __name__ == "__main__":
    unittest.main()
