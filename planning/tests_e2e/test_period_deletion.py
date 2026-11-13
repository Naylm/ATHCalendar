import re
import unittest

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


class PeriodDeletionE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if browser_tests() is None:
            raise unittest.SkipTest("playwright non disponible")

    def test_bureau_deletes_period_with_typed_confirmation(self):
        with LiveServer() as server, browser_tests()() as pw:
            server.seed(seed_period_script())
            server.seed(SEED_BUREAU)
            browser = pw.chromium.launch()
            try:
                context = browser.new_context(
                    viewport={"width": 390, "height": 844},
                    is_mobile=True,
                    has_touch=True,
                )
                tracked = TrackedPage(context.new_page())
                page = tracked.page
                page.goto(server.url + "/bureau/connexion/")
                page.locator('input[name="password"]').fill(BUREAU_PASSWORD)
                page.get_by_role("button", name="Connexion", exact=True).click()
                expect(page).to_have_url(server.url + "/bureau/")
                page.get_by_role("link", name="Gérer", exact=True).first.click()
                page.get_by_text("Options", exact=True).click()
                page.get_by_role("link", name="Supprimer la période", exact=True).click()
                heading = page.get_by_role("heading", name="Supprimer définitivement")
                expect(heading).to_be_visible()
                expect(page.get_by_text("Recopiez exactement : Semestre test")).to_be_visible()
                check_no_horizontal_overflow(page)
                page.screenshot(path=str(SCREENSHOT_DIR / "v022-period-delete-confirm-390.png"))

                # Mauvais libellé : refusé, formulaire réinitialisé sur la même page.
                page.locator("#id_confirmation_label").fill("Mauvais libellé")
                page.locator("#id_confirm_delete").check()
                page.get_by_role("button", name="Supprimer définitivement").click()
                expect(heading).to_be_visible()
                expect(page).to_have_url(re.compile(r"/supprimer/$"))

                # Bon libellé + case cochée : suppression effective.
                page.locator("#id_confirmation_label").fill("Semestre test")
                page.locator("#id_confirm_delete").check()
                page.get_by_role("button", name="Supprimer définitivement").click()
                expect(page).to_have_url(server.url + "/bureau/")
                page.goto(server.url)
                expect(page.get_by_text("Semestre test")).to_have_count(0)
                check_no_horizontal_overflow(page)
                tracked.assert_clean()
            finally:
                browser.close()
