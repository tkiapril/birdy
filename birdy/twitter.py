from requests.auth import HTTPBasicAuth
from requests_oauthlib import OAuth1Session, OAuth2Session
from oauthlib.oauth2 import BackendApplicationClient
from oauthlib.common import to_unicode
from . import __version__

import requests
import json
import collections

DEFAULT_API_VERSION = '1.1'
DEFAULT_API_ENDPOINT_FORMAT = 'https://{endpoint}.twitter.com'
DEFAULT_USER_AGENT_STRING = 'Birdy Twitter Client v{}'.format(__version__)


class BirdyException(Exception):
    def __init__(
        self,
        msg,
        resource_url=None,
        request_method=None,
        status_code=None,
        error_code=None,
        headers=None,
    ):
        self._msg = msg
        self.request_method = request_method
        self.resource_url = resource_url
        self.status_code = status_code
        self.error_code = error_code
        self.headers = headers

    def __str__(self):
        if self.request_method and self.resource_url:
            return '{} ({} {})'.format(
                self._msg,
                self.request_method,
                self.resource_url,
            )
        return self._msg


class TwitterClientError(BirdyException):
    pass


class TwitterApiError(BirdyException):
    def __init__(
        self,
        msg,
        response=None,
        request_method=None,
        error_code=None,
    ):
        kwargs = {'request_method': request_method}

        if response is not None:
            kwargs.update(
                {
                    'status_code': response.status_code,
                    'resource_url': response.url,
                    'headers': response.headers,
                }
            )

        super(TwitterApiError, self).__init__(msg, **kwargs)


class TwitterRateLimitError(TwitterApiError):
    pass


class TwitterAuthError(TwitterApiError):
    pass


class ApiComponent(object):
    def __init__(self, client, path=None):
        self._client = client
        self._path = path

    def __repr__(self):
        return '<ApiComponent: {}>'.format(self._path)

    def __getitem__(self, path):
        if self._path is not None:
            path = '{}/{}'.format(self._path, path)
        return ApiComponent(self._client, path)

    def __getattr__(self, path):
        return self[path]

    def get(self, **params):
        if self._path is None:
            raise TypeError(
                'Calling get() on an empty API path is not supported.'
            )
        return self._client.request('GET', self._path, **params)

    def post(self, **params):
        if self._path is None:
            raise TypeError(
                'Calling post() on an empty API path is not supported.'
            )
        return self._client.request('POST', self._path, **params)

    def get_path(self):
        return self._path


class BaseResponse(object):
    def __repr__(self):
        return '<{}: {} {}>'.format(
            self.__class__.__name__,
            self.request_method,
            self.resource_url,
        )


class ApiResponse(BaseResponse):
    def __init__(self, response, request_method, json_data):
        self.resource_url = response.url
        self.headers = response.headers
        self.request_method = request_method
        self.data = json_data


class StreamResponse(BaseResponse):
    def __init__(self, response, request_method, json_object_hook):
        self.resource_url = response.url
        self.headers = response.headers
        self.request_method = request_method
        self._stream_iter = response.iter_lines
        self._json_object_hook = json_object_hook

    def stream(self):
        for item in self._stream_iter():
            if item:
                try:
                    data = json.loads(
                        item,
                        object_hook=self._json_object_hook,
                    )
                except:
                    pass
                else:
                    yield data


class JSONObject(dict):
    def __getattr__(self, name):
        if name in iter(self.keys()):
            return self[name]
        raise AttributeError(
            '{} has no property named {}.'.format(
                self.__class__.__name__,
                name,
            )
        )

    def __setattr__(self, *args):
        raise AttributeError(
            '{} instances are read-only.'.format(self.__class__.__name__)
        )
    __delattr__ = __setitem__ = __delitem__ = __setattr__

    def __repr__(self):
        return '<{}: {}>'.format(self.__class__.__name__, dict.__repr__(self))


class BaseTwitterClient(object):
    def __init__(
        self,
        api_version=DEFAULT_API_VERSION,
        api_endpoint_format=DEFAULT_API_ENDPOINT_FORMAT,
        user_agent_string=DEFAULT_USER_AGENT_STRING,
    ):
        self.api_version = api_version
        self.api_endpoint_format = api_endpoint_format
        self.user_agent_string = user_agent_string

    def __getattr__(self, path):
        return ApiComponent(self, path)

    def configure_oauth_session(self, session):
        session.headers = {'User-Agent': self.get_user_agent_string()}
        return session

    def get_user_agent_string(self):
        return self.user_agent_string

    def request(self, method, path, **params):
        method = method.upper()
        url = self.construct_resource_url(path)
        request_kwargs = {}
        params, files = self.sanitize_params(params)

        if method == 'GET':
            request_kwargs['params'] = params
        elif method == 'POST':
            request_kwargs['data'] = params
            request_kwargs['files'] = files

        try:
            response = self.make_api_call(method, url, **request_kwargs)
        except requests.RequestException as e:
            raise TwitterClientError(str(e), url, method)

        return self.handle_response(method, response)

    def construct_resource_url(self, path):
        paths = path.split('/')
        return '{}/{}/{}.json'.format(
            self.api_endpoint_format.format(endpoint=paths[0]),
            self.api_version,
            '/'.join(paths[1:]),
        )

    def make_api_call(self, method, url, **request_kwargs):
        return self.session.request(method, url, **request_kwargs)

    def handle_response(self, method, response):
        try:
            data = response.json(object_hook=self.get_json_object_hook)
        except ValueError:
            data = None

        if response.status_code == 200:
            return ApiResponse(response, method, data)

        if data is None:
            raise TwitterApiError(
                'Unable to decode JSON response.',
                response,
                method,
            )

        error_code, error_msg = self.get_twitter_error_details(data)

        if (
            response.status_code == 401 or
            'Bad Authentication data' in error_msg
        ):
            raise TwitterAuthError(error_msg, response, method, error_code)

        elif response.status_code == 404:
            raise TwitterApiError(
                'Invalid API resource.',
                response,
                method,
                error_code,
            )

        elif response.status_code == 429:
            raise TwitterRateLimitError(
                error_msg,
                response,
                method,
                error_code,
            )

        raise TwitterApiError(error_msg, response, method, error_code)

    @staticmethod
    def sanitize_params(input_params):
        params, files = ({}, {})

        for k, v in input_params.items():
            if hasattr(v, 'read') and isinstance(v.read, collections.Callable):
                files[k] = v
            elif isinstance(v, bool):
                if v:
                    params[k] = 'true'
                else:
                    params[k] = 'false'
            elif isinstance(v, list):
                params[k] = ','.join(v)
            else:
                params[k] = v
        return params, files

    @staticmethod
    def get_json_object_hook(data):
        return JSONObject(data)

    @staticmethod
    def get_twitter_error_details(data):
        code, msg = (
            None,
            'An unknown error has occured processing your request.'
        )
        errors = data.get('errors') if data else None

        if errors and isinstance(errors, list):
            code = errors[0]['code']
            msg = errors[0]['message']
        elif errors:
            code = errors['code']
            msg = errors['message']

        return (code, msg)


class UserClient(BaseTwitterClient):
    def __init__(
        self,
        consumer_key,
        consumer_secret,
        access_token=None,
        access_token_secret=None,
        api_version=DEFAULT_API_VERSION,
        api_endpoint_format=DEFAULT_API_ENDPOINT_FORMAT,
        user_agent_string=DEFAULT_USER_AGENT_STRING,
    ):
        super().__init__(
            api_version, api_endpoint_format, user_agent_string
        )
        self.request_token_url = (
            '{}/oauth/request_token'.format(
                self.api_endpoint_format.format(endpoint='api')
            )
        )
        self.access_token_url = (
            '{}/oauth/access_token'.format(
                self.api_endpoint_format.format(endpoint='api')
            )
        )
        self.base_signin_url = (
            '{}/oauth/authenticate'.format(
                self.api_endpoint_format.format(endpoint='api')
            )
        )
        self.base_authorize_url = (
            '{}/oauth/authorize'.format(
                self.api_endpoint_format.format(endpoint='api')
            )
        )

        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.access_token = access_token
        self.access_token_secret = access_token_secret

        self.session = self.get_oauth_session()

    def get_oauth_session(self):
        return self.configure_oauth_session(
            OAuth1Session(
                client_key=self.consumer_key,
                client_secret=self.consumer_secret,
                resource_owner_key=self.access_token,
                resource_owner_secret=self.access_token_secret,
            )
        )

    def get_signin_token(
        self,
        callback_url=None,
        auto_set_token=True,
        **kwargs
    ):
        return self.get_request_token(
            self.base_signin_url,
            callback_url,
            auto_set_token,
            **kwargs
        )

    def get_authorize_token(
        self,
        callback_url=None,
        auto_set_token=True,
        **kwargs
    ):
        return self.get_request_token(
            self.base_authorize_url,
            callback_url,
            auto_set_token,
            **kwargs
        )

    def get_request_token(
        self,
        base_auth_url=None,
        callback_url=None,
        auto_set_token=True,
        **kwargs
    ):
        if callback_url:
            self.session._client.client.callback_uri = \
                to_unicode(callback_url, 'utf-8')

        try:
            token = self.session.fetch_request_token(self.request_token_url)
        except requests.RequestException as e:
            raise TwitterClientError(str(e))
        except ValueError as e:
            raise TwitterClientError('Response does not contain a token.')

        if base_auth_url:
            token['auth_url'] = self.session.authorization_url(
                base_auth_url,
                **kwargs
            )

        if auto_set_token:
            self.auto_set_token(token)

        return JSONObject(token)

    def get_access_token(self, oauth_verifier, auto_set_token=True):
        required = (self.access_token, self.access_token_secret)

        if not all(required):
            raise TwitterClientError(
                '{} must be initialized with access_token and '
                'access_token_secret to fetch authorized '
                'access token.'.format(
                    self.__class__.__name__
                )
            )

        self.session._client.client.verifier = \
            to_unicode(oauth_verifier, 'utf-8')

        try:
            token = self.session.fetch_access_token(self.access_token_url)
        except requests.RequestException as e:
            raise TwitterClientError(str(e))
        except ValueError:
            raise TwitterClientError('Reponse does not contain a token.')

        if auto_set_token:
            self.auto_set_token(token)

        return JSONObject(token)

    def auto_set_token(self, token):
        self.access_token = token['oauth_token']
        self.access_token_secret = token['oauth_token_secret']
        self.session = self.get_oauth_session()


class AppClient(BaseTwitterClient):
    def __init__(
        self,
        consumer_key,
        consumer_secret,
        access_token=None,
        token_type='bearer',
        api_version=DEFAULT_API_VERSION,
        api_endpoint_format=DEFAULT_API_ENDPOINT_FORMAT,
        user_agent_string=DEFAULT_USER_AGENT_STRING,
    ):
        super().__init__(
            api_version, api_endpoint_format, user_agent_string
        )
        self.request_token_url = '{}/oauth2/token'.format(
            self.api_endpoint_format.format(endpoint='api')
        )
        self.invalidate_token_url = (
            '{}/oauth2/invalidate_token'.format(
                self.api_endpoint_format.format(endpoint='api')
            )
        )

        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.access_token = access_token
        self.token_type = token_type

        self.session = self.get_oauth_session()
        self.auth = HTTPBasicAuth(self.consumer_key, self.consumer_secret)

    def get_oauth_session(self):
        client = BackendApplicationClient(self.consumer_key)
        token = None

        if self.access_token:
            token = {
                'access_token': self.access_token,
                'token_type': self.token_type,
            }

        return self.configure_oauth_session(
            OAuth2Session(
                client=client,
                token=token,
            )
        )

    def get_access_token(self, auto_set_token=True):
        data = {'grant_type': 'client_credentials'}

        try:
            response = self.session.post(
                self.request_token_url,
                auth=self.auth,
                data=data,
            )
            data = json.loads(response.content.decode('utf-8'))
            access_token = data['access_token']
        except requests.RequestException as e:
            raise TwitterClientError(str(e))
        except (ValueError, KeyError):
            raise TwitterClientError(
                'Response does not contain an access token.'
            )

        if auto_set_token:
            self.access_token = access_token
            self.session = self.get_oauth_session()

        return access_token

    def invalidate_access_token(self):
        data = {'access_token': self.access_token}

        try:
            response = self.session.post(
                self.invalidate_token_url,
                auth=self.auth,
                data=data,
            )
        except requests.RequestException as e:
            raise TwitterClientError(str(e))
        else:
            if response.status_code == 200:
                access_token = self.access_token
                self.access_token = None
                self.session = self.get_oauth_session()
                return access_token

        raise TwitterClientError('Could not invalidate access token.')


class StreamClient(BaseTwitterClient):
    def __init__(
        self,
        consumer_key,
        consumer_secret,
        access_token,
        access_token_secret,
        api_version=DEFAULT_API_VERSION,
        api_endpoint_format=DEFAULT_API_ENDPOINT_FORMAT,
        user_agent_string=DEFAULT_USER_AGENT_STRING,
    ):
        super().__init__(
            api_version, api_endpoint_format, user_agent_string
        )
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.access_token = access_token
        self.access_token_secret = access_token_secret

        self.session = self.get_oauth_session()

    def get_oauth_session(self):
        return self.configure_oauth_session(
            OAuth1Session(
                client_key=self.consumer_key,
                client_secret=self.consumer_secret,
                resource_owner_key=self.access_token,
                resource_owner_secret=self.access_token_secret,
            )
        )

    def make_api_call(self, method, url, **request_kwargs):
        return self.session.request(method, url, stream=True, **request_kwargs)

    def handle_response(self, method, response):
        if response.status_code == 200:
            return StreamResponse(response, method, self.get_json_object_hook)

        elif response.status_code == 401:
            raise TwitterAuthError(
                'Unauthorized.',
                response,
                method,
                response.status_code,
            )

        elif response.status_code == 404:
            raise TwitterApiError(
                'Invalid API resource.',
                response,
                method,
                response.status_code,
            )

        elif response.status_code == 420:
            raise TwitterRateLimitError(
                response.content,
                response,
                method,
                response.status_code,
            )

        raise TwitterApiError(
            response.content,
            response,
            method,
            response.status_code,
        )
