from requests_oauthlib import OAuth1Session
import requests

REQUEST_TOKEN_URL = "https://authentication.fatsecret.com/oauth/request_token"
AUTHORIZE_URL = "https://authentication.fatsecret.com/oauth/authorize"
ACCESS_TOKEN_URL = "https://authentication.fatsecret.com/oauth/access_token"
API_URL = "https://platform.fatsecret.com/rest/server.api"

class FatSecretClient:
    def __init__(self, settings, store):
        self.settings = settings
        self.store = store

    def begin_authorization(self, callback_url: str) -> str:
        oauth = OAuth1Session(
            self.settings.fatsecret_consumer_key,
            client_secret=self.settings.fatsecret_consumer_secret,
            callback_uri=callback_url,
            signature_method="HMAC-SHA1",
            signature_type="BODY",
        )
        tokens = oauth.fetch_request_token(REQUEST_TOKEN_URL)
        self.store.put_pending(tokens["oauth_token"], tokens["oauth_token_secret"])
        return oauth.authorization_url(AUTHORIZE_URL)

    def finish_authorization(self, request_token: str, verifier: str):
        request_secret = self.store.get_pending(request_token)
        if not request_secret:
            raise RuntimeError("Unknown or expired OAuth request token")
        oauth = OAuth1Session(
            self.settings.fatsecret_consumer_key,
            client_secret=self.settings.fatsecret_consumer_secret,
            resource_owner_key=request_token,
            resource_owner_secret=request_secret,
            verifier=verifier,
            signature_method="HMAC-SHA1",
            signature_type="BODY",
        )
        tokens = oauth.fetch_access_token(ACCESS_TOKEN_URL)
        self.store.put_secret("access_token", tokens["oauth_token"])
        self.store.put_secret("access_secret", tokens["oauth_token_secret"])
        self.store.delete_pending(request_token)

    def _api(self, method: str, params: dict):
        token = self.store.get_secret("access_token")
        secret = self.store.get_secret("access_secret")
        if not token or not secret:
            raise RuntimeError("FatSecret account is not connected. Use /connect.")
        oauth = OAuth1Session(
            self.settings.fatsecret_consumer_key,
            client_secret=self.settings.fatsecret_consumer_secret,
            resource_owner_key=token,
            resource_owner_secret=secret,
            signature_method="HMAC-SHA1",
            signature_type="BODY",
        )
        payload = {"method": method, "format": "json", **params}
        response = oauth.post(API_URL, data=payload, timeout=25)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and "error" in data:
            raise RuntimeError(str(data["error"]))
        return data

    def get_diary(self, date_int: int):
        return self._api("food_entries.get.v2", {"date": date_int})

    def search_food(self, query: str, region: str = "UA"):
        return self._api("foods.search.v2", {
            "search_expression": query, "max_results": 10,
            "flag_default_serving": "true", "region": region
        })

    def add_entry(self, food_id: str, serving_id: str, food_name: str,
                  units: float, meal: str, day: int):
        return self._api("food_entry.create", {
            "food_id": food_id, "serving_id": serving_id,
            "food_entry_name": food_name, "number_of_units": units,
            "meal": meal, "date": day
        })

    def edit_entry(self, entry_id: str, changes: dict):
        params = {"food_entry_id": entry_id}
        mapping = {"food_name": "food_entry_name", "serving_id": "serving_id",
                   "units": "number_of_units", "meal": "meal"}
        for source, target in mapping.items():
            if source in changes and changes[source] is not None:
                params[target] = changes[source]
        return self._api("food_entry.edit", params)

    def delete_entry(self, entry_id: str):
        return self._api("food_entry.delete", {"food_entry_id": entry_id})
