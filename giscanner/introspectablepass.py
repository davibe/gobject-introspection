# -*- Mode: Python -*-
# Copyright (C) 2010 Red Hat, Inc.
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place - Suite 330,
# Boston, MA 02111-1307, USA.
#

from . import ast
from . import glibast
from . import message

class IntrospectablePass(object):

    def __init__(self, transformer):
        self._transformer = transformer
        self._namespace = transformer.namespace

    # Public API

    def validate(self):
        self._namespace.walk(self._analyze_node)
        self._namespace.walk(self._introspectable_callable_analysis)
        self._namespace.walk(self._introspectable_callable_analysis)
        self._namespace.walk(self._introspectable_pass3)

    def _interface_vfunc_check(self, node, stack):
        if isinstance(node, glibast.GLibInterface):
            for vfunc in node.virtual_methods:
                if not vfunc.invoker:
                    message.warn_node(vfunc,
"""Virtual function %r has no known invoker""" % (vfunc.name, ),
                    context=node)

    def _parameter_warning(self, parent, param, text, *args):
        if hasattr(parent, 'symbol'):
            prefix = '%s: ' % (parent.symbol, )
        else:
            prefix = ''
        if isinstance(param, ast.Parameter):
            context = "argument %s: " % (param.argname, )
        else:
            context = "return value: "
        message.warn_node(parent, prefix + context + text, *args)

    def _introspectable_param_analysis(self, parent, node):
        is_return = isinstance(node, ast.Return)
        is_parameter = isinstance(node, ast.Parameter)
        assert is_return or is_parameter

        if node.type.target_giname is not None:
            target = self._transformer.lookup_typenode(node.type)
        else:
            target = None

        if not node.type.resolved:
            self._parameter_warning(parent, node,
"Unresolved type: %r" % (node.type.unresolved_string, ))
            parent.introspectable = False
            return

        if isinstance(node.type, ast.Varargs):
            parent.introspectable = False
            return

        if not isinstance(node.type, ast.List) and \
                (node.type.target_giname == 'GLib.List'):
            self._parameter_warning(parent, node, "Missing (element-type) annotation")
            parent.introspectable = False
            return

        if (is_parameter
            and isinstance(target, ast.Callback)
            and not node.type.target_giname in ('GLib.DestroyNotify',
                                                'Gio.AsyncReadyCallback')
            and node.scope is None):
                self._parameter_warning(parent, node,
                    ("Missing (scope) annotation for callback" +
                     " without GDestroyNotify (valid: %s, %s)")
                     % (ast.PARAM_SCOPE_CALL, ast.PARAM_SCOPE_ASYNC))
                parent.introspectable = False
                return

        if is_return and isinstance(target, ast.Callback):
            self._parameter_warning(parent, node, "Callbacks cannot be return values; use (skip)")
            parent.introspectable = False
            return

        if (is_return
            and isinstance(target, (ast.Record, ast.Union))
            and not target.foreign
            and not isinstance(target, glibast.GLibBoxed)):
            if node.transfer != ast.PARAM_TRANSFER_NONE:
                self._parameter_warning(parent, node,
"Invalid non-constant return of bare structure or union; register as boxed type or (skip)")
                parent.introspectable = False
            return

        if node.transfer is None:
            self._parameter_warning(parent, node, "Missing (transfer) annotation")
            parent.introspectable = False
            return

    def _type_is_introspectable(self, typeval, warn=False):
        if not typeval.resolved:
            return False
        if isinstance(typeval, ast.TypeUnknown):
            return False
        if isinstance(typeval, (ast.Array, ast.List)):
            return self._type_is_introspectable(typeval.element_type)
        elif isinstance(typeval, ast.Map):
            return (self._type_is_introspectable(typeval.key_type)
                    and self._type_is_introspectable(typeval.value_type))
        if typeval.target_foreign:
            return True
        if typeval.target_fundamental:
            if typeval.is_equiv(ast.TYPE_VALIST):
                return False
            # Mark UCHAR as not introspectable temporarily until
            # we're ready to land the typelib changes
            if typeval.is_equiv(ast.TYPE_UNICHAR):
                return False
            # These are not introspectable pending us adding
            # larger type tags to the typelib (in theory these could
            # be 128 bit or larger)
            if typeval.is_equiv((ast.TYPE_LONG_LONG, ast.TYPE_LONG_ULONG,
                                 ast.TYPE_LONG_DOUBLE)):
                return False
            return True
        target = self._transformer.lookup_typenode(typeval)
        if not target:
            return False
        return target.introspectable and (not target.skip)

    def _analyze_node(self, obj, stack):
        if obj.skip:
            return False
        # Combine one-pass checks here
        self._interface_vfunc_check(obj, stack)
        # Our first pass for scriptability
        if isinstance(obj, ast.Callable):
            for param in obj.parameters:
                self._introspectable_param_analysis(obj, param)
            self._introspectable_param_analysis(obj, obj.retval)
        if isinstance(obj, (ast.Class, ast.Interface, ast.Record, ast.Union)):
            for field in obj.fields:
                if field.type:
                    if not self._type_is_introspectable(field.type):
                        field.introspectable = False
        return True

    def _introspectable_callable_analysis(self, obj, stack):
        if obj.skip:
            return False
        # Propagate introspectability of parameters to entire functions
        if isinstance(obj, ast.Callable):
            for param in obj.parameters:
                if not self._type_is_introspectable(param.type):
                    obj.introspectable = False
                    return True
            if not self._type_is_introspectable(obj.retval.type):
                obj.introspectable = False
                return True
        return True

    def _introspectable_pass3(self, obj, stack):
        if obj.skip:
            return False
        # Propagate introspectability for fields
        if isinstance(obj, (ast.Class, ast.Interface, ast.Record, ast.Union)):
            for field in obj.fields:
                if field.anonymous_node:
                    if not field.anonymous_node.introspectable:
                        field.introspectable = False
                else:
                    if not self._type_is_introspectable(field.type):
                        field.introspectable = False
        # Propagate introspectability for properties
        if isinstance(obj, (ast.Class, ast.Interface)):
            for prop in obj.properties:
                if not self._type_is_introspectable(prop.type):
                    prop.introspectable = False
            for sig in obj.signals:
                self._introspectable_callable_analysis(sig, [obj])
        return True