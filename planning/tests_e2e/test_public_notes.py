import unittest

from playwright.sync_api import expect

from planning.tests_e2e.test_ui import (
    SCREENSHOT_DIR,
    LiveServer,
    TrackedPage,
    browser_tests,
    check_no_horizontal_overflow,
    seed_period_script,
)

NOTE_EXAMPLE = "Besoin d’un/une expérimenté(e)"


class PublicNotesE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if browser_tests() is None:
            raise unittest.SkipTest("playwright non disponible")

    def test_save_reload_and_clear_at_all_widths(self):
        with LiveServer() as server, browser_tests()() as pw:
            server.seed(seed_period_script())
            browser = pw.chromium.launch()
            try:
                for width in (320, 390, 1440):
                    context = browser.new_context(
                        viewport={"width": width, "height": 900},
                        is_mobile=width < 768,
                        has_touch=width < 768,
                    )
                    tracked = TrackedPage(context.new_page())
                    page = tracked.page
                    page.goto(server.url)
                    form = page.locator(".event-note-form").first
                    ta = form.locator("textarea")
                    save = form.locator(".note-save")
                    cancel = form.locator(".note-cancel")
                    # Vide et inactif : champ d'une ligne, boutons masqués,
                    # exemple en espace réservé jamais prérempli.
                    expect(ta).to_have_value("")
                    assert ta.evaluate("el => el.defaultValue") == ""
                    expect(ta).to_have_attribute(
                        "placeholder", "Ex. : besoin d’un/une expérimenté(e)"
                    )
                    expect(save).to_be_hidden()
                    expect(save).to_be_disabled()
                    expect(cancel).to_be_hidden()
                    page.locator("h1").click()  # blur avant mesure
                    assert ta.bounding_box()["height"] <= 30
                    check_no_horizontal_overflow(page)
                    page.screenshot(path=str(SCREENSHOT_DIR / f"v025-planning-empty-{width}.png"))
                    ta.fill(NOTE_EXAMPLE)
                    expect(save).to_be_visible()
                    expect(save).to_be_enabled()
                    expect(cancel).to_be_visible()
                    with page.expect_response(
                        lambda r: (
                            r.request.method == "PUT" and "/note/" in r.url and r.status == 200
                        )
                    ):
                        save.click()
                    expect(save).to_be_hidden()
                    expect(save).to_be_disabled()
                    expect(cancel).to_be_hidden()
                    check_no_horizontal_overflow(page)
                    page.locator("h1").click()  # blur avant capture
                    page.screenshot(path=str(SCREENSHOT_DIR / f"v025-planning-note-{width}.png"))
                    page.reload()
                    expect(page.locator(".event-note-form textarea").first).to_have_value(
                        NOTE_EXAMPLE
                    )
                    ta = page.locator(".event-note-form textarea").first
                    ta.fill("")
                    with page.expect_response(
                        lambda r: (
                            r.request.method == "PUT" and "/note/" in r.url and r.status == 200
                        )
                    ):
                        page.locator(".event-note-form .note-save").first.click()
                    expect(page.locator(".event-note-form .note-save").first).to_be_disabled()
                    page.reload()
                    expect(page.locator(".event-note-form textarea").first).to_have_value("")
                    tracked.assert_clean()
                    context.close()
            finally:
                browser.close()

    def test_concurrent_edit_keeps_draft_until_cancel(self):
        with LiveServer() as server, browser_tests()() as pw:
            server.seed(seed_period_script())
            browser = pw.chromium.launch()
            try:
                ctx_b = browser.new_context(viewport={"width": 390, "height": 844})
                tracked_b = TrackedPage(ctx_b.new_page())
                page_b = tracked_b.page
                page_b.goto(server.url)
                form_b = page_b.locator(".event-note-form").first
                ta_b = form_b.locator("textarea")
                ta_b.fill("brouillon B")
                page_b.locator("h1").click()  # blur

                ctx_a = browser.new_context(viewport={"width": 390, "height": 844})
                tracked_a = TrackedPage(ctx_a.new_page())
                page_a = tracked_a.page
                page_a.goto(server.url)
                form_a = page_a.locator(".event-note-form").first
                form_a.locator("textarea").fill("note de A")
                with page_a.expect_response(
                    lambda r: r.request.method == "PUT" and "/note/" in r.url and r.status == 200
                ):
                    form_a.locator(".note-save").click()

                # B reçoit le nouveau HTML : brouillon et version de base conservés.
                page_b.evaluate("window.ath.live.refresh(true)")
                expect(ta_b).to_have_value("brouillon B")
                base_version = form_b.get_attribute("data-note-version")
                assert base_version == "1", base_version

                with page_b.expect_response(
                    lambda r: r.request.method == "PUT" and "/note/" in r.url and r.status == 409
                ):
                    form_b.locator(".note-save").click()
                expect(form_b.locator(".note-msg")).to_contain_text("brouillon")
                expect(ta_b).to_have_value("brouillon B")

                # Une synchronisation ultérieure préserve l'alerte et la
                # consigne de récupération ; brouillon et base inchangés.
                page_b.evaluate("window.ath.live.refresh(true)")
                msg = form_b.locator(".note-msg")
                expect(msg).to_contain_text("Annuler")
                expect(msg).to_have_attribute("role", "alert")
                expect(ta_b).to_have_value("brouillon B")
                assert form_b.get_attribute("data-note-version") == "1"

                form_b.locator(".note-cancel").click()
                expect(page_b.locator(".event-note-form textarea").first).to_have_value("note de A")
                form_b = page_b.locator(".event-note-form").first
                assert form_b.get_attribute("data-note-version") == "2"
                form_b.locator("textarea").fill("brouillon B final")
                with page_b.expect_response(
                    lambda r: r.request.method == "PUT" and "/note/" in r.url and r.status == 200
                ):
                    form_b.locator(".note-save").click()
                tracked_a.assert_clean()
                tracked_b.assert_clean(allow_409=True)
                ctx_a.close()
                ctx_b.close()
            finally:
                browser.close()

    def test_locked_and_hidden_rows_keep_draft_readonly(self):
        with LiveServer() as server, browser_tests()() as pw:
            server.seed(seed_period_script())
            server.seed(
                "from planning.models import Event\n"
                "from planning.services import events\n"
                "from planning.services.actors import Actor\n"
                "ev = Event.objects.order_by('date').first()\n"
                "events.update_event(ev.pk, actor=Actor.bureau(), public_note='Note initiale')\n"
            )
            browser = pw.chromium.launch()
            try:
                context = browser.new_context(
                    viewport={"width": 390, "height": 844},
                    is_mobile=True,
                    has_touch=True,
                )
                tracked = TrackedPage(context.new_page())
                page = tracked.page
                page.goto(server.url)
                row_id = page.locator(".event-card").first.get_attribute("id")
                row = page.locator("#" + row_id)
                form = row.locator(".event-note-form")
                ta = form.locator("textarea")
                expect(ta).to_have_value("Note initiale")
                ta.fill("brouillon verrouillé")

                # Le Bureau ferme la date : le brouillon reste, en lecture seule.
                server.seed(
                    "from planning.models import Event\n"
                    "from planning.services import events\n"
                    "from planning.services.actors import Actor\n"
                    "ev = Event.objects.order_by('date').first()\n"
                    "events.set_event_availability(ev.pk, actor=Actor.bureau(),"
                    " availability='full', expected_version=ev.version)\n"
                )
                page.evaluate("window.ath.live.refresh(true)")
                expect(ta).to_have_value("brouillon verrouillé")
                expect(ta).to_have_attribute("readonly", "")
                expect(form.locator(".note-save")).to_be_disabled()
                expect(form.locator(".note-msg")).to_contain_text("verrouillée")
                form.locator(".note-cancel").click()
                expect(row.locator(".event-note")).to_contain_text("Note initiale")

                # Réouverture, nouveau brouillon puis masquage : la ligne
                # conservée garde le brouillon en lecture seule.
                server.seed(
                    "from planning.models import Event\n"
                    "from planning.services import events\n"
                    "from planning.services.actors import Actor\n"
                    "ev = Event.objects.order_by('date').first()\n"
                    "events.set_event_availability(ev.pk, actor=Actor.bureau(),"
                    " availability='available', expected_version=ev.version)\n"
                )
                page.evaluate("window.ath.live.refresh(true)")
                form = row.locator(".event-note-form")
                form.locator("textarea").fill("brouillon masqué")
                server.seed(
                    "from planning.models import Event\n"
                    "from planning.services import events\n"
                    "from planning.services.actors import Actor\n"
                    "ev = Event.objects.order_by('date').first()\n"
                    "events.set_event_hidden(ev.pk, actor=Actor.bureau(),"
                    " is_hidden=True, expected_version=ev.version)\n"
                )
                page.evaluate("window.ath.live.refresh(true)")
                expect(form.locator("textarea")).to_have_value("brouillon masqué")
                expect(form.locator("textarea")).to_have_attribute("readonly", "")
                form.locator(".note-cancel").click()
                expect(row).to_have_count(0)
                check_no_horizontal_overflow(page)
                tracked.assert_clean()
                context.close()
            finally:
                browser.close()
