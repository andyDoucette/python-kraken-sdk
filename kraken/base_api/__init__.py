#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2023 Benjamin Thomas Schwertfeger
# Github: https://github.com/btschwertfeger

"""Module that implements the base classes for all Spot and Futures clients"""

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from copy import deepcopy
from typing import Any, Dict, List, Optional, Type, Union
from urllib.parse import urljoin
from uuid import uuid1

import requests

from kraken.exceptions import KrakenException


class KrakenErrorHandler:
    """
    Class that checks if the response of a request contains error messages and
    returns either message if there is no error or raises a custom KrakenException
    based on the error message.
    """

    def __init__(self: "KrakenErrorHandler") -> None:
        self.__kexceptions: KrakenException = KrakenException()

    def __get_exception(self: "KrakenErrorHandler", msg: str) -> Optional[Any]:
        """
        Must be called when an error was found in the message, so the corresponding
        KrakenException will be returned.
        """
        return self.__kexceptions.get_exception(msg)

    def check(self: "KrakenErrorHandler", data: dict) -> Union[dict, Any]:
        """
        Check if the error message is a known KrakenError response and than raise
        a custom exception or return the data containing the "error".
        This is only for the Spot REST endpoints, since the Futures API
        serves kinds of errors.

        :param data: The response as dict to check for an error
        :type data: dict
        :raise kraken.exceptions.KrakenException.*: raises a KrakenError if the response contains an error
        :return: The response as dict
        :rtype: dict
        :raises KrakenError: If is the error keyword in the response
        """
        if len(data.get("error", [])) == 0 and "result" in data:
            return data["result"]

        exception = self.__get_exception(data["error"])
        if exception:
            raise exception(data)
        return data

    def check_send_status(self: "KrakenErrorHandler", data: dict) -> dict:
        """
        Checks the responses of Futures REST endpoints

        :param data: The response as dict to check for an error
        :type data: dict
        :raise kraken.exceptions.KrakenException.*: raises a KrakenError if the response contains an error
        :return: The response as dict
        :rtype: dict
        """
        if "sendStatus" in data and "status" in data["sendStatus"]:
            exception: Type[KrakenException] = self.__get_exception(
                data["sendStatus"]["status"]
            )
            if exception:
                raise exception(data)
            return data
        return data

    def check_batch_status(self: "KrakenErrorHandler", data: dict) -> dict:
        """
        Used to check the Futures batch order responses for errors

        :param data: The response as dict to check for an error
        :type data: dict
        :raise kraken.exceptions.KrakenException.*: raises a KrakenError if the response contains an error
        :return: The response as List[dict]
        :rtype: dict
        """
        if "batchStatus" in data:
            batch_status: List[Dict[str, Any]] = data["batchStatus"]
            for status in batch_status:
                if "status" in status:
                    exception: Type[KrakenException] = self.__get_exception(
                        status["status"]
                    )
                    if exception:
                        raise exception(data)
        return data


class KrakenBaseSpotAPI:
    """
    This class the the base for all Spot clients, handles un-/signed
    requests and returns exception handled results.

    :param key: Spot API public key (default: ``""``)
    :type key: str, optional
    :param secret: Spot API secret key (default: ``""``)
    :type secret: str, optional
    :param url: URL to access the Kraken API (default: https://api.kraken.com)
    :type url: str, optional
    :param sandbox: Use the sandbox (not supported for Spot trading so far, default: ``False``)
    :type sandbox: bool, optional
    """

    URL: str = "https://api.kraken.com"
    API_V: str = "/0"

    def __init__(
        self: "KrakenBaseSpotAPI",
        key: str = "",
        secret: str = "",
        url: str = "",
        sandbox: bool = False,
        use_custom_exceptions: bool = True,
    ):
        if sandbox:
            raise ValueError("Sandbox not available for Kraken Spot trading.")
        if url != "":
            self.url = url
        else:
            self.url = urljoin(self.URL, self.API_V)

        self.__nonce: int = 0
        self.__key: str = key
        self.__secret: str = secret
        self.__use_custom_exceptions: bool = use_custom_exceptions

        self.__err_handler: KrakenErrorHandler = KrakenErrorHandler()
        self.__session: requests.Session = requests.Session()
        self.__session.headers.update({"User-Agent": "python-kraken-sdk"})

    def _request(
        self: "KrakenBaseSpotAPI",
        method: str,
        uri: str,
        timeout: int = 10,
        auth: bool = True,
        params: Optional[dict] = None,
        do_json: bool = False,
        return_raw: bool = False,
    ) -> Union[Dict[str, Any], List[str], List[Dict[str, Any]], requests.Response]:
        """
        Handles the requested requests, by sending the request, handling the response,
        and returning the message or in case of an error the respective Exception.

        :param method:  The request method, e.g., ``GET``, ``POST``, and ``PUT``
        :type method: str
        :param uri: The endpoint to send the message
        :type uri: str
        :param timeout: Timeout for the request (default: ``10``)
        :type timeout: int
        :param auth: If the requests needs authentication (default: ``True``)
        :type auth: bool
        :param params: The query or post prameter of the request (default: ``None``)
        :type params: Optional[dict]
        :param do_json: If the ``params`` must be "jsonified" - in case of nested dict style
        :type do_json: bool
        :param return_raw: If the response should be returned without parsing. This is used
         for example when requesting an export of the trade history as .zip archive.
        :type return_raw: bool
        :raise kraken.exceptions.KrakenException.*: If the response contains errors
        :return: The response
        :rtype: Union[Dict[str, Any], List[str], List[Dict[str, Any]], requests.Response]
        """
        if params is None:
            params = {}

        method = method.upper()
        data_json: str = ""
        if method in ("GET", "DELETE"):
            if params:
                strl: List[str] = [f"{key}={params[key]}" for key in sorted(params)]
                data_json = "&".join(strl)
                uri += f"?{data_json}".replace(" ", "%20")

        headers: dict = {}
        if auth:
            if (
                not self.__key
                or self.__key == ""
                or not self.__secret
                or self.__secret == ""
            ):
                raise ValueError("Missing credentials.")
            self.__nonce = (self.__nonce + 1) % 1
            params["nonce"] = str(int(time.time() * 1000)) + str(self.__nonce).zfill(4)

            content_type: str
            sign_data: str

            if do_json:
                content_type = "application/json; charset=utf-8"
                sign_data = json.dumps(params)
            else:
                content_type = "application/x-www-form-urlencoded; charset=utf-8"
                sign_data = urllib.parse.urlencode(params)

            headers.update(
                {
                    "Content-Type": content_type,
                    "API-Key": self.__key,
                    "API-Sign": self._get_kraken_signature(
                        urlpath=f"{self.API_V}{uri}",
                        data=sign_data,
                        nonce=params["nonce"],
                    ),
                }
            )

        url: str = f"{self.url}{uri}"
        if method in ("GET", "DELETE"):
            return self.__check_response_data(
                self.__session.request(
                    method=method, url=url, headers=headers, timeout=timeout
                ),
                return_raw,
            )

        if do_json:
            return self.__check_response_data(
                self.__session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=params,
                    timeout=timeout,
                ),
                return_raw,
            )

        return self.__check_response_data(
            self.__session.request(
                method=method, url=url, headers=headers, data=params, timeout=timeout
            ),
            return_raw,
        )

    def _get_kraken_signature(
        self: "KrakenBaseSpotAPI", urlpath: str, data: str, nonce: int
    ) -> str:
        """
        Creates the signature of the data. This is requred for authenticated requests
        to verify the user.

        :param urlpath: The endpont including the api version
        :type urlpath: str
        :param data: Data of the request to sign, including the nonce.
        :type data: str
        :param nonce: The nonce to sign with
        :type nonce: int
        :return: The signed string
        :rtype: str
        """
        return base64.b64encode(
            hmac.new(
                base64.b64decode(self.__secret),
                urlpath.encode()
                + hashlib.sha256((str(nonce) + data).encode()).digest(),
                hashlib.sha512,
            ).digest()
        ).decode()

    def __check_response_data(
        self: "KrakenBaseSpotAPI", response: requests.Response, return_raw: bool = False
    ) -> Union[dict, list, requests.Response]:
        """
        Checkes the response, handles the error (if exists) and returns the response data.

        :param response: The response of a request, requested by the requests module
        :type response: requests.Response
        :param return_raw: Defines if the return should be the raw response if there is no error
        :type data: bool
        :return: The reponse in raw or parsed to dict
        :rtype: Union[dict, list, requests.Response]
        """
        if not self.__use_custom_exceptions:
            return response

        if response.status_code in ("200", 200):
            if return_raw:
                return response
            try:
                data: Union[dict, list] = response.json()
            except ValueError as exc:
                raise ValueError(response.content) from exc

            if "error" in data:
                # can only be dict if error is present:
                return self.__err_handler.check(data)  # type: ignore[arg-type]
            return data

        raise Exception(f"{response.status_code} - {response.text}")

    @property
    def return_unique_id(self: "KrakenBaseSpotAPI") -> str:
        """Returns a unique uuid string

        :return: uuid
        :rtype: str
        """
        return "".join(str(uuid1()).split("-"))

    def _to_str_list(self: "KrakenBaseSpotAPI", value: Union[str, list]) -> str:
        """
        Converts a list to a comme separated string

        :param value: The value to convert to e.g., ["XBT", "USD"] => "XBT,USD"
        :type value: Union[str,dict]
        :return: The content ov `value` as comma-separated string
        :rtype: str
        """
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return ",".join(value)
        raise ValueError("a must be type of str or list of strings")

    def __enter__(self: "KrakenBaseSpotAPI") -> "KrakenBaseSpotAPI":
        return self

    def __exit__(
        self: "KrakenBaseSpotAPI", *exc: tuple, **kwargs: Dict[str, Any]
    ) -> None:
        pass


class KrakenBaseFuturesAPI:
    """
    The base class for all Futures clients handles un-/signed requests
    and returns exception handled results.

    If the sandbox environment is chosen, the keys must be generated from here:
        https://demo-futures.kraken.com/settings/api

    :param key: Futures API public key (default: ``""``)
    :type key: str, optional
    :param secret: Futures API secret key (default: ``""``)
    :type secret: str, optional
    :param url: The URL to access the Futures Kraken API (default: https://futures.kraken.com)
    :type url: str, optional
    :param sandbox: If set to ``True`` the URL will be https://demo-futures.kraken.com (default: ``False``)
    :type sandbox: bool, optional
    """

    URL: str = "https://futures.kraken.com"
    SANDBOX_URL: str = "https://demo-futures.kraken.com"

    def __init__(
        self: "KrakenBaseFuturesAPI",
        key: str = "",
        secret: str = "",
        url: str = "",
        sandbox: bool = False,
        use_custom_exceptions: bool = True,
    ):
        self.sandbox: bool = sandbox
        self.url: str
        if url:
            self.url = url
        elif self.sandbox:
            self.url = self.SANDBOX_URL
        else:
            self.url = self.URL

        self.__key: str = key
        self.__secret: str = secret
        self.__nonce: int = 0
        self.__use_custom_exceptions: bool = use_custom_exceptions

        self.__err_handler: KrakenErrorHandler = KrakenErrorHandler()
        self.__session: requests.Session = requests.Session()
        self.__session.headers.update({"User-Agent": "python-kraken-sdk"})

    def _request(
        self: "KrakenBaseFuturesAPI",
        method: str,
        uri: str,
        timeout: int = 10,
        auth: bool = True,
        post_params: Optional[dict] = None,
        query_params: Optional[dict] = None,
        return_raw: bool = False,
    ) -> Union[Dict[str, Any], List[Dict[str, Any]], List[str], requests.Response]:
        """
        Handles the requested requests, by sending the request, handling the response,
        and returning the message or in case of an error the respective Exception.

        :param method:  The request method, e.g., ``GET``, ``POST``, and ``PUT``
        :type method: str
        :param uri: The endpoint to send the message
        :type uri: str
        :param timeout: Timeout for the request (default: ``10``)
        :type timeout: int
        :param auth: If the request needs authentication (default: ``True``)
        :type auth: bool
        :param post_params: The query prameter of the request (default: ``None``)
        :type post_params: Optional[dict]
        :param query_params: The query prameter of the request (default: ``None``)
        :type query_params: Optional[dict]
        :param do_json: If the ``post_params`` must be "jsonified" - in case of nested dict style
        :type do_json: bool
        :param return_raw: If the response should be returned without parsing.
            This is used for example when requesting an export of the trade history as .zip archive.
        :type return_raw: bool
        :raise kraken.exceptions.KrakenException.*: If the response contains errors
        :return: The response
        :rtype: Union[Dict[str, Any], List[Dict[str, Any]], List[str], requests.Response]
        """
        method = method.upper()

        post_string: str = ""
        strl: List[str]
        if post_params is not None:
            strl = [f"{key}={post_params[key]}" for key in sorted(post_params)]
            post_string = "&".join(strl)
        else:
            post_params = {}

        query_string: str = ""
        if query_params is not None:
            strl = [f"{key}={query_params[key]}" for key in sorted(query_params)]
            query_string = "&".join(strl).replace(" ", "%20")
        else:
            query_params = {}

        headers: dict = {}
        if auth:
            if (
                not self.__key
                or self.__key == ""
                or not self.__secret
                or self.__secret == ""
            ):
                raise ValueError("Missing credentials")
            self.__nonce = (self.__nonce + 1) % 1
            nonce: str = str(int(time.time() * 1000)) + str(self.__nonce).zfill(4)
            headers.update(
                {
                    "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                    "Nonce": nonce,
                    "APIKey": self.__key,
                    "Authent": self._get_kraken_futures_signature(
                        uri, query_string + post_string, nonce
                    ),
                }
            )

        if method in ("GET", "DELETE"):
            return self.__check_response_data(
                self.__session.request(
                    method=method,
                    url=f"{self.url}{uri}"
                    if query_string == ""
                    else f"{self.url}{uri}?{query_string}",
                    headers=headers,
                    timeout=timeout,
                ),
                return_raw,
            )

        if method == "PUT":
            return self.__check_response_data(
                self.__session.request(
                    method=method,
                    url=f"{self.url}{uri}",
                    params=str.encode(post_string),
                    headers=headers,
                    timeout=timeout,
                ),
                return_raw,
            )

        return self.__check_response_data(
            self.__session.request(
                method=method,
                url=f"{self.url}{uri}?{post_string}",
                data=str.encode(post_string),
                headers=headers,
                timeout=timeout,
            ),
            return_raw,
        )

    def _get_kraken_futures_signature(
        self: "KrakenBaseFuturesAPI", endpoint: str, data: str, nonce: str
    ) -> str:
        """
        Creates the signature of the data. This is requred for authenticated requests
        to verify the user.

        :param endpoint: The endpont including the api version
        :type endpoint: str
        :param data: Data of the request to sign, including the nonce.
        :type data: dict
        :param nonce: The nonce to use for this signature
        :type nonce: str
        :return: The signed string
        :rtype: str
        """
        if endpoint.startswith("/derivatives"):
            endpoint = endpoint[len("/derivatives") :]

        sha256_hash = hashlib.sha256()
        sha256_hash.update((data + nonce + endpoint).encode("utf8"))
        return base64.b64encode(
            hmac.new(
                base64.b64decode(self.__secret), sha256_hash.digest(), hashlib.sha512
            ).digest()
        ).decode()

    def __check_response_data(
        self: "KrakenBaseFuturesAPI",
        response: requests.Response,
        return_raw: bool = False,
    ) -> Union[dict, requests.Response]:
        """
        Checkes the response, handles the error (if exists) and returns the response data.

        :param response: The response of a request, requested by the requests module
        :type response: requests.Response
        :param return_raw: Defines if the return should be the raw response if there is no error
        :type return_raw: dict
        :raise kraken.exceptions.KrakenException.*: If the response contains the error key
        :return: The signed string
        :rtype: Union[dict, Response]
        """
        if not self.__use_custom_exceptions:
            return response

        if response.status_code in ("200", 200):
            if return_raw:
                return response
            try:
                data: dict = response.json()
            except ValueError as exc:
                raise ValueError(response.content) from exc

            if "error" in data:
                return self.__err_handler.check(data)
            if "sendStatus" in data:
                return self.__err_handler.check_send_status(data)
            if "batchStatus" in data:
                return self.__err_handler.check_batch_status(data)
            return data

        raise Exception(f"{response.status_code} - {response.text}")

    def __enter__(self: "KrakenBaseFuturesAPI") -> "KrakenBaseFuturesAPI":
        return self

    def __exit__(self, *exc: tuple, **kwargs: Dict[str, Any]) -> None:
        pass
