from requests_oauthlib import OAuth1Session

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

    @staticmethod
    def _extract(data, root_key, item_key):
        root = data.get(root_key) or {}
        if not isinstance(root, dict):
            return []
        value = root.get(item_key)
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    def get_saved_meals_with_items(self):
        # Cookbook-to-Saved-Meals mapping must be verified on the connected account.
        data = self._api("saved_meals.get.v2", {})
        meals = self._extract(data, "saved_meals", "saved_meal")
        result = []
        for meal in meals:
            if not isinstance(meal, dict):
                continue
            meal_id = meal.get("saved_meal_id")
            items = []
            if meal_id:
                response = self._api("saved_meal_items.get.v2", {"saved_meal_id": meal_id})
                items = self._extract(response, "saved_meal_items", "saved_meal_item")
            result.append({
                "name": meal.get("saved_meal_name") or meal.get("name"),
                "description": meal.get("saved_meal_description") or meal.get("description"),
                "items": [{
                    "name": item.get("saved_meal_item_name") or item.get("food_name"),
                    "number_of_units": item.get("number_of_units"),
                    "food_id": item.get("food_id"),
                    "serving_id": item.get("serving_id"),
                } for item in items if isinstance(item, dict)]
            })
        return result
