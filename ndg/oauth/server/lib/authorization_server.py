"""OAuth 2.0 WSGI server middleware implements support for basic bearer 
tokens and also X.509 certificates as access tokens

OAuth 2.0 Authorisation Server
"""
__author__ = "R B Wilkinson"
__date__ = "12/12/11"
__copyright__ = "(C) 2011 Science and Technology Facilities Council"
__license__ = "BSD - see LICENSE file in top-level directory"
__contact__ = "Philip.Kershaw@stfc.ac.uk"
__revision__ = "$Id$"

import json
import logging
import httplib
import urllib

from ndg.oauth.server.lib.access_token.make_access_token import \
                                                    make_access_token
from ndg.oauth.server.lib.oauth.access_token import AccessTokenRequest
from ndg.oauth.server.lib.oauth.authorize import (AuthorizeRequest, 
                                                  AuthorizeResponse)
from ndg.oauth.server.lib.oauth.oauth_exception import OauthException
from ndg.oauth.server.lib.register.access_token import AccessTokenRegister
from ndg.oauth.server.lib.register.client import ClientRegister
from ndg.oauth.server.lib.register.authorization_grant import \
                                                    AuthorizationGrantRegister

log = logging.getLogger(__name__)


class AuthorizationServer(object):
    """
    Provides the core OAuth 2.0 server functions.
    """
    AUTHZ_HDR_ENV_KEYNAME = 'HTTP_AUTHORIZATION'
    BEARER_TOK_ID = 'Bearer'
    MAC_TOK_ID = 'MAC'
    TOKEN_TYPES = (BEARER_TOK_ID, MAC_TOK_ID)
    
    def __init__(self, client_register_file, authorizer, client_authenticator,
                 access_token_generator, config):
        self.client_register = ClientRegister(client_register_file)
        self.authorizer = authorizer
        self.client_authenticator = client_authenticator
        self.access_token_generator = access_token_generator
        self.access_token_register = AccessTokenRegister(config)
        self.authorization_grant_register = AuthorizationGrantRegister(config)

    def authorize(self, request, client_authorized):
        """Handle an authorization request.

        It is assumed that the caller has checked whether the user is
        authenticated and that the user has authorised the client and scope.

        Request query parameters (from 
        http://tools.ietf.org/html/draft-ietf-oauth-v2-22):

        response_type
              REQUIRED.  Value MUST be set to "code".
        client_id
              REQUIRED.  The client identifier as described in Section 2.2.
        redirect_uri
              OPTIONAL, as described in Section 3.1.2.
        scope
              OPTIONAL.  The scope of the access request as described by
              Section 3.3.
        state
              RECOMMENDED.  An opaque value used by the client to maintain
              state between the request and callback.  The authorization
              server includes this value when redirecting the user-agent back
              to the client.  The parameter SHOULD be used for preventing
              cross-site request forgery as described in Section 10.12.

        Response:
              application/x-www-form-urlencoded format:
        code
              REQUIRED.  The authorization code generated by the
              authorization server.  The authorization code MUST expire
              shortly after it is issued to mitigate the risk of leaks.  A
              maximum authorization code lifetime of 10 minutes is
              RECOMMENDED.  The client MUST NOT use the authorization code
              more than once.  If an authorization code is used more than
              once, the authorization server MUST deny the request and SHOULD
              attempt to revoke all tokens previously issued based on that
              authorization code.  The authorization code is bound to the
              client identifier and redirection URI.
        state
              REQUIRED if the "state" parameter was present in the client
              authorization request.  The exact value received from the
              client.

        @type request: webob.Request
        @param request: HTTP request object

        @type client_authorized: bool
        @param client_authorized: True if resource owner has authorized client

        @rtype: tuple: (str, int, str)
        @return: tuple (
                     redirect_uri
                     HTTP status if error
                     error description
                 )
        """
        log.debug("Starting authorization request")

        # Parameters should only be taken from the query string.
        params = request.GET
        auth_request = AuthorizeRequest(params.get('response_type', None),
                                        params.get('client_id', None),
                                        params.get('redirect_uri', None),
                                        params.get('scope', None),
                                        params.get('state', None))

        try:
            self.check_request(request, params, post_only=False)

            # Check for required parameters.
            required_parameters = ['response_type', 'client_id']
            for param in required_parameters:
                if param not in params:
                    log.error("Missing request parameter %s from params: %s",
                              param, params)
                    raise OauthException('invalid_request', 
                                         "Missing request parameter: %s" % param)

            if not client_authorized:
                raise OauthException('access_denied', 
                                     'User has declined authorization')

            response_type = params.get('response_type', None)
            if response_type != 'code':
                raise OauthException('unsupported_response_type', 
                                     "Response type %s not supported" % 
                                     response_type)

            client_error = self.client_register.is_valid_client(
                                                    auth_request.client_id, 
                                                    auth_request.redirect_uri)
            if client_error:
                log.error("Invalid client: %s", client_error)
                return (None, httplib.BAD_REQUEST, client_error)

            # redirect_uri must be included in the request if the client has
            # more than one registered.
            client = self.client_register.register[auth_request.client_id]
            if len(client.redirect_uris) != 1 and not auth_request.redirect_uri:
                log.error("An authorization request has been made without a "
                          "return URI")
                return (None, 
                        httplib.BAD_REQUEST, 
                        ('An authorization request has been made without a '
                        'return URI.'))

            # Preconditions satisfied - generate grant.
            (grant, code) = self.authorizer.generate_authorization_grant(
                                                                auth_request, 
                                                                request)
            auth_response = AuthorizeResponse(code, auth_request.state)

            if not self.authorization_grant_register.add_grant(grant):
                log.error('Registering grant failed')
                raise OauthException('server_error', 
                                     'Authorization grant could not be created')
        except OauthException, exc:
            log.error("Redirecting back after error: %s - %s", 
                      exc.error, exc.error_description)
            
            return self._redirect_after_authorize(auth_request, None, exc.error,
                                                  exc.error_description)

        log.debug("Redirecting back after successful authorization.")
        return self._redirect_after_authorize(auth_request, auth_response)

    def _redirect_after_authorize(self, 
                                  auth_request, 
                                  auth_response=None, 
                                  error=None, 
                                  error_description=None):
        """Redirects to the redirect URI after the authorization process as
        completed.

        @type resp: ndg.oauth.server.lib.oauth.authorize.AuthorizeRequest
        @param resp: OAuth authorize request
        
        @type resp: ndg.oauth.server.lib.oauth.authorize.AuthorizeResponse
        @param resp: OAuth authorize response

        @type error: str
        @param error: OAuth error

        @type error_description: str
        @param error_description: error description
        """
        # Check for inconsistencies that should be reported directly to the user.
        if not auth_response and not error:
            error = 'server_error'
            error_description = 'Internal server error'

        # Get the redirect URI.
        client = self.client_register.register[auth_request.client_id]
        redirect_uri = (
            auth_request.redirect_uri if auth_request.redirect_uri else \
                client.redirect_uris[0]
        )
        if not redirect_uri:
            return (
                None, 
                httplib.BAD_REQUEST,
                'An authorization request has been made without a return URI.')

        # Redirect back to client with authorization code or error.
        if error:
            url_parameters = [('error', error), 
                              ('error_description', error_description)]
        else:
            url_parameters = [('code', auth_response.code)]
            
        full_redirect_uri = self._make_combined_url(redirect_uri, 
                                                    url_parameters, 
                                                    auth_request.state)
        log.debug("Redirecting to URI: %s", full_redirect_uri)
        return(full_redirect_uri, None, None)

    @staticmethod
    def _make_combined_url(base_url, parameters, state):
        """Constructs a URL from a base URL and parameters to be included in a
        query string.
        @type base_url: str
        @param base_url: base URL to which to add query parameters

        @type parameters: dict
        @param parameters: parameter names and values

        @type state: str
        @param state: OAuth state parameter value, which whould not be URL
        encoded

        @rtype: str
        @return: full URL
        """
        url = base_url.rstrip('?')
        url_parts = [url]
        sep_with_ampersand = ('?' in url)
        if parameters:
            query_string = urllib.urlencode(parameters)
            url_parts.extend([('&' if (sep_with_ampersand) else '?'), 
                              query_string])
            sep_with_ampersand = True

        if state:
            url_parts.extend([('&' if (sep_with_ampersand) else '?'), 
                              'state=',
                              state])

        return ''.join(url_parts)


    def access_token(self, request):
        """
        Handles a request for an access token.

        Request parameters in post data (from 
        http://tools.ietf.org/html/draft-ietf-oauth-v2-22):

        The client makes a request to the token endpoint by adding the
        following parameters using the "application/x-www-form-urlencoded"
        format in the HTTP request entity-body:

        grant_type
              REQUIRED.  Value MUST be set to "authorization_code".
        code
              REQUIRED.  The authorization code received from the
              authorization server.
        redirect_uri
              REQUIRED, if the "redirect_uri" parameter was included in the
              authorization request as described in Section 4.1.1, and their
              values MUST be identical.

        Response:
              application/json format:
        access_token
              access token
        token_type
              token type
        expires_in
              lifetime of token in seconds
        refresh_token

        @type request: webob.Request
        @param request: HTTP request object

        @rtype: tuple: (str, int, str)
        @return: tuple (
                     OAuth JSON response
                     HTTP status if error
                     error description
                 )
        """
        log.debug("Starting access token request")

        try:
            # Parameters should only be taken from the body, not the URL query 
            # string.
            params = request.POST
            self.check_request(request, params, post_only=True)

            # Check that the client is authenticated as a registered client.
            client_id = self.client_authenticator.authenticate(request)
            if client_id is None:
                log.warn('Client authentication not performed')
            else:
                log.debug("Client id: %s", client_id)

            # redirect_uri is only required if it was included in the 
            # authorization request.
            required_parameters = ['grant_type', 'code']
            for param in required_parameters:
                if param not in params:
                    log.error("Missing request parameter %s from inputs: %s",
                              param, params)
                    raise OauthException(
                                    'invalid_request', 
                                    "Missing request parameter: %s" % param)

        except OauthException, exc:
            return (self._error_access_token_response(exc.error, 
                                                      exc.error_description), 
                    None, None)

        token_request = AccessTokenRequest(params.get('grant_type', None),
                                           params.get('code', None),
                                           params.get('redirect_uri', None))

        try:
            response = make_access_token(
                token_request, client_id, self.access_token_register,
                self.access_token_generator, self.authorization_grant_register,
                request)
        except OauthException, exc:
            return (self._error_access_token_response(exc.error, 
                                                      exc.error_description), 
                    None, None)

        if response:
            return self._access_token_response(response), None, None
        else:
            return (None, httplib.INTERNAL_SERVER_ERROR, 
                    'Access token generation failed.')

    def _access_token_response(self, resp):
        """Constructs the JSON response to an access token request.
        @type resp: ndg.oauth.server.lib.oauth.access_token.AccessTokenResponse
        @param resp: OAuth access token response

        @rtype: str
        @return JSON formatted response
        """
        log.debug("Responding successfully with access token.")
        content_dict = resp.get_as_dict()
        content = json.dumps(content_dict)
        return content

    def _error_access_token_response(self, error, error_description):
        """Constructs an error JSON response to an access token request.
        @type error: str
        @param error: OAuth error

        @type error_description: str
        @param error_description: error description

        @rtype: str
        @return JSON formatted response
        """
        log.error("Responding with error: %s - %s", error, error_description)
        error_dict = {'error': error}
        if error_description:
            error_dict['error_description'] = error_description
        error_content = json.dumps(error_dict)
        return error_content

    def check_request(self, request, params, post_only=False):
        """
        Checks that the request is valid in the following respects:
        o Must be over HTTPS.
        o Optionally, must use the POST method.
        o Parameters must not be repeated.
        If the request is directly from the client, the user must be
        authenticated - it is assumed that the caller has checked this.

        Raises OauthException if any check fails.

        @type request: webob.Request
        @param request: HTTP request object

        @type params: dict
        @param params: request parameters

        @type post_only: bool
        @param post_only: True if the HTTP method must be POST, otherwise False


        """
        if request.scheme != 'https':
            raise OauthException('invalid_request', 
                                 'Transport layer security must be used for '
                                 'this request.')

        if post_only and (request.method != 'POST'):
            raise OauthException('invalid_request', 
                                 'HTTP POST method must be used for this '
                                 'request.')

        # Check for duplicate parameters.
        param_counts = {}
        for key in params.iterkeys():
            count = param_counts.get(key, 0)
            param_counts[key] = count + 1
        for key, count in param_counts.iteritems():
            if count > 1:
                raise OauthException('invalid_request', 
                                     'Parameter "%s" is repeated.' % key)
        return

    def check_token(self, request, scope=None):
        """
        Simple service that could be used to validate bearer tokens. It would
        be called from a resource service that trusts this authorization
        service. This is not part of the OAuth specification.

        Request parameters

        access_token
              REQUIRED.  Bearer token
        scope
              OPTIONAL.  Scope 

        Response:
              application/json format:
        status
              HTTP status indicating the access control decision
        error
              error as described in
              http://tools.ietf.org/html/draft-ietf-oauth-v2-22#section-5.2

        @type request: webob.Request
        @param request: HTTP request object

        @type scope: str
        @param scope: required scope

        @rtype: tuple: (str, int, str)
        @return: tuple (
                     OAuth JSON response
                     HTTP status
                     error description
                 )
        """
        params = request.params
        if 'access_token' not in params:
            error = 'invalid_request'
        else:
            access_token = params['access_token']
            if scope:
                required_scope = scope
            else:
                required_scope = params.get('scope', None)
            error = self.access_token_register.get_token(access_token,
                                                         required_scope)[-1]

        status = {'invalid_request': httplib.BAD_REQUEST,
                  'invalid_token': httplib.FORBIDDEN,
                  None: httplib.OK}.get(error, httplib.BAD_REQUEST)

        content_dict = {'status': status}
        if error:
            content_dict['error'] = error
        content = json.dumps(content_dict)
        return (content, status, error)

    def get_registered_token(self, request, scope=None):
        """
        Checks that a token in the request is valid. It would
        be called from a resource service that trusts this authorization
        service. 

        Request parameters:
              set in Authorization header (OAuth spec., Section 7.1 Access
              Token Types
        token type: Bearer or MAC
        access token: access token to obtain access

        Response:
              application/json format:
        status
              HTTP status indicating the access control decision
        error
              error as described in
              http://tools.ietf.org/html/draft-ietf-oauth-v2-22#section-5.2

        @type request: webob.Request
        @param request: HTTP request object

        @type scope: str
        @param scope: required scope

        @rtype: tuple: (str, int, str)
        @return: tuple (
                     access token
                     HTTP status
                     error description
                 )
        """
        authorization_hdr = request.environ.get(
                                        self.__class__.AUTHZ_HDR_ENV_KEYNAME)
        if authorization_hdr is None:
            log.error('No Authorization header present for request to %r',
                      request.path_url)
            error = 'invalid_request'
            token = None            
        else:
            authorization_hdr_parts = authorization_hdr.split()
            if len(authorization_hdr_parts) < 2:
                log.error('Expecting at least two Authorization header '
                          'elements for request to %r; '
                          'header is: %r', request.path_url, authorization_hdr)
                error = 'invalid_request'            
                
            token_type, access_token = authorization_hdr_parts[:2]
            
            # Currently only supports bearer type tokens
            if token_type != self.__class__.BEARER_TOK_ID:
                log.error('Token type retrieved is %r, expecting "Bearer" '
                          'type for request to %r', token_type)
                error = 'invalid_request'
            else:   
                token, error = self.access_token_register.get_token(
                                                                access_token, 
                                                                scope)

        status = {'invalid_request': httplib.BAD_REQUEST,
                  'invalid_token': httplib.FORBIDDEN,
                  'insufficient_scope': httplib.FORBIDDEN,
                  None: httplib.OK}.get(error, httplib.BAD_REQUEST)

        return token, status, error

    def is_registered_client(self, request):
        """Determines whether the client ID in the request is registered.
        @type request: WebOb.request
        @param request: request
        @rtype: tuple (basestring, basestring) or (NoneType, NoneType)
        @return: (error, error description) or None if client ID is found and
        registered
        """
        client_id = request.params.get('client_id', None)
        if not client_id:
            return 'invalid_request', 'Missing request parameter: client_id'
        else:
            error_description = self.client_register.is_registered_client(
                                                                    client_id)
            if error_description:
                return 'unauthorized_client', error_description
            
        return None, None
