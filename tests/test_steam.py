import unittest

from ccpt.cmd.m import convert_steam_url


class ConvertSteamUrlTests(unittest.TestCase):
  def test_custom_profile(self):
    self.assertEqual(
      convert_steam_url("https://steamcommunity.com/id/example_name/"),
      "steam://url/CommunityFilePage/example_name",
    )

  def test_numeric_profile(self):
    self.assertEqual(
      convert_steam_url(
        "https://steamcommunity.com/profiles/76561198000000000/"
      ),
      "steam://url/SteamIDPage/76561198000000000",
    )

  def test_store_page(self):
    self.assertEqual(
      convert_steam_url("https://store.steampowered.com/app/730/CounterStrike_2/"),
      "steam://store/730",
    )

  def test_run_store_page_or_id(self):
    self.assertEqual(
      convert_steam_url("run https://store.steampowered.com/app/730/"),
      "steam://rungameid/730",
    )
    self.assertEqual(convert_steam_url("run 730"), "steam://rungameid/730")

  def test_community_game_page_runs_game(self):
    self.assertEqual(
      convert_steam_url("https://steamcommunity.com/app/730/"),
      "steam://rungameid/730",
    )

  def test_community_pages(self):
    cases = {
      "https://steamcommunity.com/my/inventory/":
        "steam://url/CommunityInventory",
      "https://steamcommunity.com/my/friends/":
        "steam://url/SteamIDFriends",
      "https://steamcommunity.com/my/tradeoffers/":
        "steam://url/OpenTradeOffers",
    }
    for source, expected in cases.items():
      with self.subTest(source=source):
        self.assertEqual(convert_steam_url(source), expected)

  def test_rejects_unrelated_or_malformed_values(self):
    for value in (
      "",
      "https://example.com/app/730",
      "https://store.steampowered.com/app/not-a-number/",
      "https://steamcommunity.com/profiles/not-a-number/",
      "run not-a-number",
    ):
      with self.subTest(value=value):
        self.assertIsNone(convert_steam_url(value))


if __name__ == "__main__":
  unittest.main()
