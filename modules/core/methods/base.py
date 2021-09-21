# -*- coding: utf-8 -*-

""" CRUD Access Methods

    @copyright: 2009-2021 (c) Sahana Software Foundation
    @license: MIT

    Permission is hereby granted, free of charge, to any person
    obtaining a copy of this software and associated documentation
    files (the "Software"), to deal in the Software without
    restriction, including without limitation the rights to use,
    copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the
    Software is furnished to do so, subject to the following
    conditions:

    The above copyright notice and this permission notice shall be
    included in all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
    EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
    OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
    NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
    HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
    WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
    FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
    OTHER DEALINGS IN THE SOFTWARE.
"""

__all__ = ("S3Method",
           )

import os
import re

from gluon import current
from gluon.storage import Storage

REGEX_FILTER = re.compile(r".+\..+|.*\(.+\).*")

# =============================================================================
class S3Method(object):
    """
        CRUD Access Method

        @note: instances of subclasses don't have any of the instance
               attributes available until they actually get invoked
               from a request - i.e. apply_method() should never be
               called directly.
    """

    def __init__(self):
        """
            Constructor
        """

        self.request = None
        self.method = None

        self.download_url = None
        self.hide_filter = False


        self.prefix = None
        self.name = None
        self.resource = None

        self.tablename = None
        self.table = None
        self.record_id = None

        self.next = None

    # -------------------------------------------------------------------------
    def __call__(self, r, method=None, widget_id=None, **attr):
        """
            Entry point for the REST interface

            @param r: the S3Request
            @param method: the method established by the REST interface
            @param widget_id: widget ID
            @param attr: dict of parameters for the method handler

            @return: output object to send to the view
        """

        # Environment of the request
        self.request = r

        # Settings
        response = current.response
        self.download_url = response.s3.download_url

        # Override request method
        if method is not None:
            self.method = method
        else:
            self.method = r.method

        # Find the target resource and record
        if r.component:
            component = r.component
            resource = component
            self.record_id = self._record_id(r)
            if not self.method:
                if component.multiple and not r.component_id:
                    self.method = "list"
                else:
                    self.method = "read"
            if component.link:
                actuate_link = r.actuate_link()
                if not actuate_link:
                    resource = component.link
        else:
            self.record_id = r.id
            resource = r.resource
            if not self.method:
                if r.id or r.method in ("read", "display"):
                    self.method = "read"
                else:
                    self.method = "list"

        self.prefix = resource.prefix
        self.name = resource.name
        self.tablename = resource.tablename
        self.table = resource.table
        self.resource = resource

        if self.method == "_init":
            # Just init, don't execute
            return None

        if r.interactive:
            # hide_filter policy:
            #
            #   None            show filters on master,
            #                   hide for components (default)
            #   False           show all filters (on all tabs)
            #   True            hide all filters (on all tabs)
            #
            #   dict(alias=setting)     setting per component, alias
            #                           None means master resource,
            #                           use special alias _default
            #                           to specify an alternative
            #                           default
            #
            hide_filter = attr.get("hide_filter")
            if isinstance(hide_filter, dict):
                component_name = r.component_name
                if component_name in hide_filter:
                    hide_filter = hide_filter[component_name]
                elif "_default" in hide_filter:
                    hide_filter = hide_filter["_default"]
                else:
                    hide_filter = None
            if hide_filter is None:
                hide_filter = r.component is not None
            self.hide_filter = hide_filter
        else:
            self.hide_filter = True

        # Apply method
        if widget_id and hasattr(self, "widget"):
            output = self.widget(r,
                                 method=self.method,
                                 widget_id=widget_id,
                                 **attr)
        else:
            output = self.apply_method(r, **attr)

            # Redirection
            if self.next and resource.lastid:
                self.next = str(self.next)
                placeholder = "%5Bid%5D"
                self.next = self.next.replace(placeholder, resource.lastid)
                placeholder = "[id]"
                self.next = self.next.replace(placeholder, resource.lastid)
            if not response.error:
                r.next = self.next

            # Add additional view variables (e.g. rheader)
            self._extend_view(output, r, **attr)

        return output

    # -------------------------------------------------------------------------
    def apply_method(self, r, **attr):
        """
            Stub, to be implemented in subclass. This method is used
            to get the results as a standalone page.

            @param r: the S3Request
            @param attr: dictionary of parameters for the method handler

            @return: output object to send to the view
        """

        output = {}
        return output

    # -------------------------------------------------------------------------
    def widget(self, r, method=None, widget_id=None, visible=True, **attr):
        """
            Stub, to be implemented in subclass. This method is used
            by other method handlers to embed this method as widget.

            @note:

                For "html" format, the widget method must return an XML
                component that can be embedded in a DIV. If a dict is
                returned, it will be rendered against the view template
                of the calling method - the view template selected by
                the widget method will be ignored.

                For other formats, the data returned by the widget method
                will be rendered against the view template selected by
                the widget method. If no view template is set, the data
                will be returned as-is.

                The widget must use the widget_id as HTML id for the element
                providing the Ajax-update hook and this element must be
                visible together with the widget.

                The widget must include the widget_id as ?w=<widget_id> in
                the URL query of the Ajax-update call, and Ajax-calls should
                not use "html" format.

                If visible==False, then the widget will initially be hidden,
                so it can be rendered empty and Ajax-load its data layer
                upon a separate refresh call. Otherwise, the widget should
                receive its data layer immediately. Widgets can ignore this
                parameter if delayed loading of the data layer is not
                all([possible, useful, supported]).

            @param r: the S3Request
            @param method: the URL method
            @param widget_id: the widget ID
            @param visible: whether the widget is initially visible
            @param attr: dictionary of parameters for the method handler

            @return: output
        """

        return None

    # -------------------------------------------------------------------------
    # Utility functions
    # -------------------------------------------------------------------------
    def _permitted(self, method=None):
        """
            Check permission for the requested resource

            @param method: method to check, defaults to the actually
                           requested method
        """

        auth = current.auth
        has_permission = auth.s3_has_permission

        r = self.request

        if not method:
            method = self.method
        if method in ("list", "datatable", "datalist"):
            # Rest handled in S3Permission.METHODS
            method = "read"

        if r.component is None:
            table = r.table
            record_id = r.id
        else:
            table = r.component.table
            record_id = r.component_id

            if method == "create":
                # Is creating a new component record allowed without
                # permission to update the master record?
                writable = current.s3db.get_config(r.tablename,
                                                   "ignore_master_access",
                                                   )
                if not isinstance(writable, (tuple, list)) or \
                   r.component_name not in writable:
                    master_access = has_permission("update",
                                                   r.table,
                                                   record_id = r.id,
                                                   )
                    if not master_access:
                        return False

        return has_permission(method, table, record_id=record_id)

    # -------------------------------------------------------------------------
    @staticmethod
    def _record_id(r):
        """
            Get the ID of the target record of a S3Request

            @param r: the S3Request
        """

        master_id = r.id

        if r.component:

            component = r.component
            component_id = r.component_id
            link = r.link

            if not component.multiple and not component_id:
                # Enforce first component record
                table = component.table
                pkey = table._id.name
                component.load(start=0, limit=1)
                if len(component):
                    component_id = component.records().first()[pkey]
                    if link and master_id:
                        r.link_id = link.link_id(master_id, component_id)
                    r.component_id = component_id
                    component.add_filter(table._id == component_id)

            if not link or r.actuate_link():
                return component_id
            else:
                return r.link_id
        else:
            return master_id

        return None

    # -------------------------------------------------------------------------
    def _config(self, key, default=None):
        """
            Get a configuration setting of the current table

            @param key: the setting key
            @param default: the default value
        """

        return current.s3db.get_config(self.tablename, key, default)

    # -------------------------------------------------------------------------
    @staticmethod
    def _view(r, default):
        """
            Get the path to the view template

            @param r: the S3Request
            @param default: name of the default view template
        """

        folder = r.folder
        prefix = r.controller

        exists = os.path.exists
        join = os.path.join

        settings = current.deployment_settings
        theme = settings.get_theme()
        theme_layouts = settings.get_theme_layouts()

        if theme != "default":
            # See if there is a Custom View for this Theme
            view = join(folder, "modules", "templates", theme_layouts, "views",
                        "%s_%s_%s" % (prefix, r.name, default))
            if exists(view):
                # There is a view specific to this page
                # NB This should normally include {{extend layout.html}}
                # Pass view as file not str to work in compiled mode
                return open(view, "rb")
            else:
                if "/" in default:
                    subfolder, default_ = default.split("/", 1)
                else:
                    subfolder = ""
                    default_ = default
                if exists(join(folder, "modules", "templates", theme_layouts, "views",
                               subfolder, "_%s" % default_)):
                    # There is a general view for this page type
                    # NB This should not include {{extend layout.html}}
                    if subfolder:
                        subfolder = "%s/" % subfolder
                    # Pass this mapping to the View
                    current.response.s3.views[default] = \
                        "../modules/templates/%s/views/%s_%s" % (theme_layouts,
                                                                 subfolder,
                                                                 default_,
                                                                 )

        if r.component:
            view = "%s_%s_%s" % (r.name, r.component_name, default)
            path = join(folder, "views", prefix, view)
            if exists(path):
                return "%s/%s" % (prefix, view)
            else:
                view = "%s_%s" % (r.name, default)
                path = join(folder, "views", prefix, view)
        else:
            view = "%s_%s" % (r.name, default)
            path = join(folder, "views", prefix, view)

        if exists(path):
            return "%s/%s" % (prefix, view)
        else:
            return default

    # -------------------------------------------------------------------------
    @staticmethod
    def _extend_view(output, r, **attr):
        """
            Add additional view variables (invokes all callables)

            @param output: the output dict
            @param r: the S3Request
            @param attr: the view variables (e.g. 'rheader')

            @note: overload this method in subclasses if you don't want
                   additional view variables to be added automatically
        """

        if r.interactive and isinstance(output, dict):
            for key in attr:
                handler = attr[key]
                if callable(handler):
                    resolve = True
                    try:
                        display = handler(r)
                    except TypeError:
                        # Argument list failure
                        # => pass callable to the view as-is
                        display = handler
                        continue
                    except:
                        # Propagate all other errors to the caller
                        raise
                else:
                    resolve = False
                    display = handler
                if isinstance(display, dict) and resolve:
                    output.update(**display)
                elif display is not None:
                    output[key] = display
                elif key in output and callable(handler):
                    del output[key]

    # -------------------------------------------------------------------------
    @staticmethod
    def _remove_filters(get_vars):
        """
            Remove all filters from URL vars

            @param get_vars: the URL vars as dict
        """

        return Storage((k, v) for k, v in get_vars.items()
                              if not REGEX_FILTER.match(k))

    # -------------------------------------------------------------------------
    @staticmethod
    def crud_string(tablename, name):
        """
            Get a CRUD info string for interactive pages

            @param tablename: the table name
            @param name: the name of the CRUD string
        """

        crud_strings = current.response.s3.crud_strings
        # CRUD strings for this table
        _crud_strings = crud_strings.get(tablename, crud_strings)
        return _crud_strings.get(name,
                                 # Default fallback
                                 crud_strings.get(name))

# END =========================================================================

