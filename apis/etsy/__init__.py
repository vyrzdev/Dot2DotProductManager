from ..baseAPI import BasePlatformAPI
from lazy.protectedModels import ProtectedResource
from etsy2 import Etsy
from etsy2.oauth import EtsyOAuthClient, EtsyOAuthHelper
from ... import productDBLogger  #
from urllib.parse import parse_qs, urlparse

requiredPermissions = ["listings_r", "listing_w"]
consumer_key = "l4opy054xmz7lolo6x68ot1k"
consumer_secret = "wn1etzuu54"
shop_id = "12703209"


class EtsyAPI(BasePlatformAPI):
    persistent_identifier = "etsy"
    webhook_enabled = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        etsyAuthToken = ProtectedResource.objects(name="etsyAuthToken").first()
        etsyAuthSecret = ProtectedResource.objects(name="etsyAuthSecret").first()
        if etsyAuthToken is None or etsyAuthSecret is None:
            loginURL, temp_oauth_token_secret = EtsyOAuthHelper.get_request_url_and_token_secret(consumer_key, consumer_secret, requiredPermissions)
            temp_oauth_token = parse_qs(urlparse(loginURL).query).get("oauth_token").pop()
            productDBLogger.warn("Etsy is not authenticated!!! Visit this URL and input the verification code to authenticate!")
            productDBLogger.warn(loginURL)
            productDBLogger.warn(temp_oauth_token)
            productDBLogger.warn(temp_oauth_token_secret)
            verificationCode = input("Verification Code> ")
            oauth_token, oauth_token_secret = EtsyOAuthHelper.get_oauth_token_via_verifier(consumer_key, consumer_secret, temp_oauth_token, temp_oauth_token_secret, verificationCode)
            etsyAuthToken = ProtectedResource(name="etsyAuthToken", value=oauth_token)
            etsyAuthSecret = ProtectedResource(name="etsyAuthSecret", value=oauth_token_secret)
            etsyAuthToken.save()
            etsyAuthSecret.save()
        etsyOAuthClient = EtsyOAuthClient(
            client_key=consumer_key,
            client_secret=consumer_secret,
            resource_owner_key=etsyAuthToken.value,
            resource_owner_secret=etsyAuthSecret.value
        )
        self.EtsyClient = Etsy(etsy_oauth_client=etsyOAuthClient)
        print(self._getListing("738914494"))
        exit()

    def getAllStockCounts(self):
        pass

    def _bulkFetchListings(self):
        finishedReading = False
        totalAmountOfResourcesFetched = 0
        page = 1
        limit = 100
        fetchedResourceJSONList = list()
        while not finishedReading:
            print(f"Requesting Page: {page}")
            responseJSON = self.EtsyClient.findAllShopListingsActive(shop_id=shop_id, limit=limit, page=page)
            totalAmountOfResourcesOnEtsy = self.EtsyClient.count
            totalAmountOfResourcesFetched += len(responseJSON)
            fetchedResourceJSONList = fetchedResourceJSONList + responseJSON
            if totalAmountOfResourcesOnEtsy == totalAmountOfResourcesFetched:
                finishedReading = True
            else:
                page += 1
                finishedReading = False
        return fetchedResourceJSONList

    def _getListing(self, listing_id):
        responseJSON = self.EtsyClient.getListing(listing_id=listing_id)
        return responseJSON
