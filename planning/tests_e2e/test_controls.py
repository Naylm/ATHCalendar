import re
import unittest
from contextlib import contextmanager

from playwright.sync_api import expect

from planning.tests_e2e.test_ui import (
    BUREAU_PASSWORD,
    SCREENSHOT_DIR,
    SEED_BUREAU,
    LiveServer,
    TrackedPage,
    browser_tests,
    check_no_horizontal_overflow,
    seed_period_script,
)


def _set_active():
    return (
        "from planning.services import periods\n"
        "from planning.services.actors import Actor\n"
        "from planning.models import Period\n"
        "p = Period.objects.order_by('start_date').first()\n"
        "periods.set_active(p.pk, actor=Actor.bureau())\n"
    )


def _register_script(event_id, name):
    return (
        "from planning.services import organizers, registrations\n"
        "from planning.services.actors import Actor\n"
        f"o = organizers.get_or_create_organizer({name!r})\n"
        f"registrations.set_registration('{event_id}', o.pk, 'active',"
        " actor=Actor.public(o))\n"
    )


class ControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if browser_tests() is None:
            raise unittest.SkipTest("playwright non disponible")

    def setUp(self):
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _server(self, seed_bureau=False):
        with LiveServer() as server:
            server.seed(seed_period_script())
            server.seed(_set_active())
            if seed_bureau:
                server.seed(SEED_BUREAU)
            yield server

    def _event_ids(self, server, count=2):
        return server.query(
            "from planning.models import Event;"
            f"print('|'.join(str(e.pk) for e in Event.objects.order_by('date')[:{count}]))"
        ).split("|")

    def _public_page(self, pw, server, width=390, height=844, browser_name="chromium"):
        browser = getattr(pw, browser_name).launch()
        context = browser.new_context(viewport={"width": width, "height": height})
        tracked = TrackedPage(context.new_page())
        tracked.page.goto(server.url + "/")
        tracked.page.wait_for_selector(".event-card", timeout=8000)
        return browser, tracked

    def _bureau_login(self, page, server):
        page.goto(server.url + "/bureau/")
        page.fill("input[type=password]", BUREAU_PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url("**/bureau/")

    def test_confirm_cancel_no_put_then_withdraw_syncs(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._event_ids(server, 1)[0]
            server.seed(_register_script(event_id, "Volontaire E2E"))
            browser = pw.chromium.launch()
            ctx_a = browser.new_context(viewport={"width": 390, "height": 844})
            ctx_b = browser.new_context(viewport={"width": 1280, "height": 800})
            page_a = TrackedPage(ctx_a.new_page()).page
            page_b = TrackedPage(ctx_b.new_page()).page
            page_a.goto(server.url + "/")
            page_b.goto(server.url + "/")
            for page in (page_a, page_b):
                expect(page.locator(f"#date-{event_id} .event-names")).to_contain_text(
                    "Volontaire E2E", timeout=8000
                )
            puts = {"n": 0}
            page_a.on(
                "request",
                lambda r: puts.__setitem__("n", puts["n"] + 1) if r.method == "PUT" else None,
            )
            dialog_state = {"accept": False}
            page_a.on(
                "dialog",
                lambda d: d.accept() if dialog_state["accept"] else d.dismiss(),
            )
            page_a.locator(f"#date-{event_id} button.remove-registration").click()
            page_a.wait_for_timeout(600)
            assert puts["n"] == 0, puts
            assert page_a.locator(f"#date-{event_id} button.remove-registration").count() == 1
            dialog_state["accept"] = True
            page_a.locator(f"#date-{event_id} button.remove-registration").click()
            for page in (page_a, page_b):
                names = page.locator(f"#date-{event_id} .event-names")
                expect(names).not_to_contain_text("Volontaire E2E", timeout=9000)
                expect(page.locator(f"#date-{event_id} .event-count")).to_contain_text(
                    "0/2", timeout=9000
                )
            assert page_a.locator(f"#date-{event_id} .status-dot.dot-none").count() == 1
            state = server.query(
                "from planning.models import Registration;print(Registration.objects.get().state)"
            )
            assert state == "withdrawn", state
            browser.close()

    def test_fill_progression_and_full_row_withdrawal(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._event_ids(server, 1)[0]
            server.seed(
                "from planning.models import Event\n"
                f"e = Event.objects.get(pk='{event_id}')\n"
                "e.min_organizers = 1\n"
                "e.capacity = 1\n"
                "e.save(update_fields=['min_organizers', 'capacity'])\n"
            )
            browser, tracked = self._public_page(pw, server)
            page = tracked.page
            row = page.locator(f"#date-{event_id}")
            row.locator(".name-registration-form input[name=name]").fill("Solo")
            page.on("dialog", lambda d: d.accept())
            row.locator(".name-registration-form button[type=submit]").click()
            expect(row.locator(".event-lock-status")).to_contain_text("Complet", timeout=8000)
            expect(row.locator(".event-count")).to_contain_text("1/1")
            assert row.locator(".status-dot.dot-sufficient").count() == 1
            assert row.locator("button.remove-registration").count() == 1
            row.locator("button.remove-registration").click()
            expect(row.locator(".event-count")).to_contain_text("0/1", timeout=9000)
            expect(row).to_contain_text("Aucun inscrit")
            assert row.locator(".name-registration-form button[type=submit]").is_enabled()
            state = server.query(
                "from planning.models import Registration;print(Registration.objects.get().state)"
            )
            assert state == "withdrawn", state
            tracked.assert_clean(allow_409=True)
            browser.close()

    def test_cancelled_row_no_remove_and_forged_put_fails(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._event_ids(server, 1)[0]
            server.seed(_register_script(event_id, "Volontaire E2E"))
            server.seed(
                "from planning.services import events\n"
                "from planning.services.actors import Actor\n"
                f"events.cancel_event('{event_id}', actor=Actor.bureau())\n"
            )
            browser, tracked = self._public_page(pw, server)
            page = tracked.page
            row = page.locator(f"#date-{event_id}")
            expect(row).to_contain_text("Annulée")
            assert row.locator("button.remove-registration").count() == 0
            reg = server.query(
                "from planning.models import Registration, Event\n"
                f"r = Registration.objects.get(event_id='{event_id}')\n"
                f"e = Event.objects.get(pk='{event_id}')\n"
                "print(r.organizer_id, r.version, e.version)"
            ).split()
            status = page.evaluate(
                """async ([eid, oid, ev, rv]) => {
                    const m = document.cookie.match(/(?:^|;\\s*)csrftoken=([^;]+)/);
                    const r = await fetch('/api/events/' + eid + '/registration/', {
                        method: 'PUT',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRFToken': decodeURIComponent(m ? m[1] : '')
                        },
                        body: JSON.stringify({
                            organizer_id: oid, state: 'withdrawn',
                            expected_event_version: parseInt(ev, 10),
                            expected_version: parseInt(rv, 10)
                        })
                    });
                    return r.status;
                }""",
                [event_id, reg[0], reg[2], reg[1]],
            )
            assert status == 409, status
            state = server.query(
                "from planning.models import Registration;print(Registration.objects.get().state)"
            )
            assert state == "active", state
            browser.close()

    def test_visibility_checkbox_hides_and_restores(self):
        with self._server(seed_bureau=True) as server, browser_tests()() as pw:
            event_a, event_b = self._event_ids(server, 2)
            server.seed(_register_script(event_b, "Volontaire E2E"))
            browser, tracked = self._public_page(pw, server)
            public = tracked.page
            ctx_b = browser.new_context(viewport={"width": 1280, "height": 800})
            bureau = TrackedPage(ctx_b.new_page()).page
            self._bureau_login(bureau, server)
            period_id = server.query(
                "from planning.models import Period;"
                "print(Period.objects.order_by('start_date').first().pk)"
            )
            bureau.goto(server.url + f"/bureau/periodes/{period_id}/")
            row_b = bureau.locator(".bureau-event", has_text="Volontaire E2E")
            box_b = row_b.locator("input[name=visible]")
            assert box_b.is_checked()
            box_b.uncheck()
            expect(row_b.locator(".visibility-status")).to_be_visible()
            expect(row_b).to_have_class(re.compile("is-hidden"))
            expect(row_b).to_contain_text("Volontaire E2E")
            expect(public.locator(f"#date-{event_b}")).to_have_count(0, timeout=9000)
            expect(public.locator(f"#date-{event_a}")).to_have_count(1)
            expect(public.locator(f"#date-{event_a} .name-registration-form")).to_have_count(1)
            box_b.check()
            expect(row_b.locator(".visibility-status")).to_be_hidden()
            expect(public.locator(f"#date-{event_b}")).to_have_count(1, timeout=9000)
            expect(public.locator(f"#date-{event_b} .event-names")).to_contain_text(
                "Volontaire E2E"
            )
            expect(public.locator(f"#date-{event_b} button.remove-registration")).to_have_count(1)
            expect(public.locator(f"#date-{event_b} .name-registration-form")).to_have_count(1)
            hidden = server.query(
                "from planning.models import Event;"
                f"print(Event.objects.get(pk='{event_b}').is_hidden)"
            )
            assert hidden == "False", hidden
            browser.close()

    def test_visibility_stale_revision_inline_error_then_retry(self):
        with self._server(seed_bureau=True) as server, browser_tests()() as pw:
            event_a, event_b = self._event_ids(server, 2)
            server.seed(_register_script(event_b, "Volontaire E2E"))
            browser, tracked = self._public_page(pw, server)
            public = tracked.page
            ctx_b = browser.new_context(viewport={"width": 1280, "height": 800})
            bureau = TrackedPage(ctx_b.new_page()).page
            self._bureau_login(bureau, server)
            period_id = server.query(
                "from planning.models import Period;"
                "print(Period.objects.order_by('start_date').first().pk)"
            )
            bureau.goto(server.url + f"/bureau/periodes/{period_id}/")
            row_b = bureau.locator(".bureau-event", has_text="Volontaire E2E")
            box_b = row_b.locator("input[name=visible]")
            server.seed(_register_script(event_a, "Alice"))
            box_b.uncheck()
            expect(row_b.locator(".visibility-msg")).to_be_visible()
            expect(row_b.locator(".visibility-msg")).to_contain_text("modifié entre-temps")
            assert box_b.is_checked()
            is_hidden_b = server.query(
                "from planning.models import Event;"
                f"print(Event.objects.get(pk='{event_b}').is_hidden)"
            )
            assert is_hidden_b == "False", is_hidden_b
            box_b.uncheck()
            expect(row_b.locator(".visibility-msg")).to_be_hidden()
            expect(row_b.locator(".visibility-status")).to_be_visible()
            expect(row_b).to_have_class(re.compile("is-hidden"))
            expect(public.locator(f"#date-{event_b}")).to_have_count(0, timeout=9000)
            is_hidden_b = server.query(
                "from planning.models import Event;"
                f"print(Event.objects.get(pk='{event_b}').is_hidden)"
            )
            assert is_hidden_b == "True", is_hidden_b
            browser.close()

    def test_withdrawal_preserves_month_draft_and_scroll(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._event_ids(server, 1)[0]
            server.seed(_register_script(event_id, "Volontaire E2E"))
            browser, tracked = self._public_page(pw, server)
            page = tracked.page
            navs = {"n": 0}
            page.on("framenavigated", lambda f: navs.__setitem__("n", navs["n"] + 1))
            other = self._event_ids(server, 2)[1]
            page.locator(f"#date-{other} .name-registration-form input[name=name]").fill(
                "Brouillon"
            )
            page.on("dialog", lambda d: d.accept())
            page.locator(f"#date-{event_id} button.remove-registration").click()
            expect(page.locator(f"#date-{event_id} .event-names")).not_to_contain_text(
                "Volontaire E2E", timeout=9000
            )
            assert navs["n"] == 0
            assert (
                page.locator(
                    f"#date-{other} .name-registration-form input[name=name]"
                ).input_value()
                == "Brouillon"
            )
            tracked.assert_clean()
            browser.close()

    def test_screenshots_status_controls_viewports(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._event_ids(server, 1)[0]
            server.seed(_register_script(event_id, "Alexandra-Trèslongnomdelafamille Xyz"))
            server.seed(_register_script(event_id, "Volontaire E2E"))
            for width, height, tag in (
                (320, 700, "320"),
                (390, 844, "390"),
                (1440, 900, "1440"),
            ):
                browser, tracked = self._public_page(pw, server, width, height)
                page = tracked.page
                check_no_horizontal_overflow(page)
                row = page.locator(f"#date-{event_id}")
                expect(row.locator(".event-count")).to_contain_text("2/2")
                btn = row.locator("button.remove-registration").first
                box = btn.bounding_box()
                if width >= 960:
                    assert 24 <= box["width"] <= 30, box
                    assert 24 <= box["height"] <= 30, box
                    border = btn.evaluate("el => getComputedStyle(el).borderTopWidth")
                    assert border == "0px", border
                    chip = row.locator(".organizer-name").nth(1).bounding_box()
                    assert chip["height"] <= 34, chip
                    font = row.locator(".organizer-label").first.evaluate(
                        "el => getComputedStyle(el).fontSize"
                    )
                    assert float(font[:-2]) >= 15, font
                else:
                    assert box["width"] >= 24, box
                page.screenshot(path=str(SCREENSHOT_DIR / f"compact-controls-{tag}.png"))
                tracked.assert_clean()
                browser.close()
            touch_browser = pw.chromium.launch()
            touch_ctx = touch_browser.new_context(
                viewport={"width": 390, "height": 844}, has_touch=True
            )
            touch = TrackedPage(touch_ctx.new_page()).page
            touch.goto(server.url + "/")
            touch.wait_for_selector(".event-card", timeout=8000)
            box = touch.locator(f"#date-{event_id} button.remove-registration").first.bounding_box()
            assert box["width"] >= 44 and box["height"] >= 44, box
            touch_browser.close()
            server.seed(
                "from planning.models import Registration\n"
                "from planning.services import organizers, registrations\n"
                "from planning.services.actors import Actor\n"
                f"Registration.objects.filter(event_id='{event_id}')"
                ".update(state='withdrawn')\n"
                "for n in ('Test', 'Test2', 'Test3'):\n"
                "    o = organizers.get_or_create_organizer(n)\n"
                f"    registrations.set_registration('{event_id}', o.pk,"
                " 'active', actor=Actor.public(o))\n"
            )
            browser, tracked = self._public_page(pw, server, 1440, 900)
            page = tracked.page
            row = page.locator(f"#date-{event_id}")
            expect(row.locator(".organizer-name")).to_have_count(3)
            height = row.bounding_box()["height"]
            assert height <= 90, height
            tops = {chip.bounding_box()["y"] for chip in row.locator(".organizer-name").all()}
            assert len(tops) == 1, tops
            check_no_horizontal_overflow(page)
            page.screenshot(path=str(SCREENSHOT_DIR / "compact-controls-desktop-three.png"))
            tracked.assert_clean()
            browser.close()

    def test_webkit_withdrawal(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._event_ids(server, 1)[0]
            server.seed(_register_script(event_id, "Volontaire E2E"))
            browser, tracked = self._public_page(pw, server, browser_name="webkit")
            page = tracked.page
            page.on("dialog", lambda d: d.accept())
            page.locator(f"#date-{event_id} button.remove-registration").click()
            expect(page.locator(f"#date-{event_id} .event-names")).not_to_contain_text(
                "Volontaire E2E", timeout=9000
            )
            state = server.query(
                "from planning.models import Registration;print(Registration.objects.get().state)"
            )
            assert state == "withdrawn", state
            browser.close()

    def test_cancel_during_withdraw_confirm_conflict_no_mutation(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._event_ids(server, 1)[0]
            server.seed(_register_script(event_id, "Volontaire E2E"))
            browser, tracked = self._public_page(pw, server)
            page = tracked.page
            row = page.locator(f"#date-{event_id}")
            row.locator(".name-registration-form input[name=name]").fill("Brouillon")

            def cancel_then_accept(dialog):
                server.seed(
                    "from planning.services import events\n"
                    "from planning.services.actors import Actor\n"
                    f"events.cancel_event('{event_id}', actor=Actor.bureau())\n"
                )
                dialog.accept()

            page.on("dialog", cancel_then_accept)
            row.locator("button.remove-registration").click()
            expect(row).to_contain_text("Annulée", timeout=9000)
            assert row.locator("button.remove-registration").count() == 0
            state = server.query(
                "from planning.models import Registration;print(Registration.objects.get().state)"
            )
            assert state == "active", state
            assert (
                row.locator(".name-registration-form input[name=name]").input_value() == "Brouillon"
            )
            assert row.locator(".name-registration-form button[type=submit]").is_disabled()
            tracked.assert_clean(allow_409=True)
            browser.close()

    def test_withdraw_focus_not_stolen_by_snapshot(self):
        with self._server() as server, browser_tests()() as pw:
            event_id, other_id = self._event_ids(server, 2)
            server.seed(_register_script(event_id, "Volontaire E2E"))
            browser = pw.chromium.launch()
            context = browser.new_context(viewport={"width": 390, "height": 844})
            tracked = TrackedPage(context.new_page())
            page = tracked.page
            held = []

            def hold(route):
                held.append(route)

            page.route("**/live/**", hold)
            page.goto(server.url + "/")
            page.wait_for_selector(".event-card", timeout=8000)
            page.on("dialog", lambda d: d.accept())
            page.locator(f"#date-{event_id} button.remove-registration").click()
            input_b = page.locator(f"#date-{other_id} .name-registration-form input[name=name]")
            input_b.fill("Autre brouillon")
            input_b.focus()
            for _ in range(60):
                for route in held:
                    held.remove(route)
                    route.continue_()
                names = page.locator(f"#date-{event_id} .event-names")
                if "Volontaire" not in (names.inner_text() or ""):
                    break
                page.wait_for_timeout(100)
            for route in held:
                route.continue_()
            expect(page.locator(f"#date-{event_id} .event-names")).not_to_contain_text(
                "Volontaire E2E", timeout=9000
            )
            active = page.evaluate(
                """() => {
                    const el = document.activeElement;
                    return {
                        tag: el && el.tagName,
                        name: el && el.getAttribute('name'),
                        row: el && el.closest('.event-card')
                            ? el.closest('.event-card').dataset.eventId : null,
                        caret: el && 'selectionStart' in el ? el.selectionStart : -1
                    };
                }"""
            )
            assert active["row"] == other_id, active
            assert active["caret"] == len("Autre brouillon"), active
            assert input_b.input_value() == "Autre brouillon"
            tracked.assert_clean()
            browser.close()

    def test_hide_keeps_dirty_row_then_unhide_restores(self):
        with self._server() as server, browser_tests()() as pw:
            event_id = self._event_ids(server, 1)[0]
            server.seed(_register_script(event_id, "Volontaire E2E"))
            browser, tracked = self._public_page(pw, server)
            page = tracked.page
            row = page.locator(f"#date-{event_id}")
            row.locator(".name-registration-form input[name=name]").fill("Brouillon")

            def set_hidden(flag):
                server.seed(
                    "from planning.services import events\n"
                    "from planning.services.actors import Actor\n"
                    "from planning.models import Event\n"
                    f"e = Event.objects.get(pk='{event_id}')\n"
                    f"events.set_event_hidden('{event_id}', actor=Actor.bureau(),"
                    f" is_hidden={flag}, expected_version=e.version)\n"
                )

            set_hidden("True")
            expect(row).to_contain_text("Date retirée", timeout=9000)
            assert row.locator(".name-registration-form button[type=submit]").is_disabled()
            assert row.locator("button.remove-registration").first.is_disabled()
            set_hidden("False")
            expect(row).not_to_contain_text("Date retirée", timeout=9000)
            expect(row).not_to_contain_text("Inscriptions fermées")
            assert (
                row.locator(".name-registration-form input[name=name]").input_value() == "Brouillon"
            )
            assert row.locator("button.remove-registration").count() == 1
            assert row.locator(".name-registration-form button[type=submit]").is_enabled()
            is_hidden = server.query(
                "from planning.models import Event;"
                f"print(Event.objects.get(pk='{event_id}').is_hidden)"
            )
            assert is_hidden == "False", is_hidden
            tracked.assert_clean()
            browser.close()
