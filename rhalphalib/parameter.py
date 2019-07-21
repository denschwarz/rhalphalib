import ROOT
import numbers
import warnings
import numpy as np


class Parameter(object):
    def __init__(self, name, value):
        self._name = name
        self._value = value
        self._hasPrior = False
        self._intermediate = False

    def __repr__(self):
        return "<%s (%s) instance at 0x%x>" % (
            self.__class__.__name__,
            self._name,
            id(self),
        )

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, name):
        self._name = name

    @property
    def value(self):
        return self._value

    @property
    def intermediate(self):
        '''
        An intermediate parameter is one that should not be explicitly rendered.
        The formula will be expanded recursively until it depends only on non-intermediate value.
        Only DependentParameters can be intermediate, hence one can modify this flag for them.
        '''
        return self._intermediate

    def hasPrior(self):
        '''
        True if the prior is not flat
        '''
        return self._hasPrior

    @property
    def combinePrior(self):
        '''
        By default assume param has no prior and we are just informing combine about it
        '''
        return 'param'

    def getDependents(self):
        return {self}

    def formula(self):
        return '{' + self._name + '}'

    def renderRoofit(self, workspace):
        raise NotImplementedError

    def _binary_op(self, opinfo, other):
        opname, op, right = opinfo
        if isinstance(other, Parameter):
            if right:
                name = other.name + opname + self.name
                out = DependentParameter(name, "{0}%s{1}" % op, other, self)
            else:
                name = self.name + opname + other.name
                out = DependentParameter(name, "{0}%s{1}" % op, self, other)
            out.intermediate = True
            return out
        elif isinstance(other, numbers.Number):
            if right:
                name = type(other).__name__ + opname + self.name
                out = DependentParameter(name, "%r%s{0}" % (other, op), self)
            else:
                name = self.name + opname + type(other).__name__
                out = DependentParameter(name, "{0}%s%r" % (op, other), self)
            out.intermediate = True
            return out
        raise TypeError("unsupported operand type(s) for %s: '%s' and '%s'" % (op, str(type(self)), str(type(other))))

    def __radd__(self, other):
        return self._binary_op(('_add_', '+', True), other)

    def __rsub__(self, other):
        return self._binary_op(('_sub_', '-', True), other)

    def __rmul__(self, other):
        return self._binary_op(('_mul_', '*', True), other)

    def __rtruediv__(self, other):
        return self._binary_op(('_div_', '/', True), other)

    def __add__(self, other):
        return self._binary_op(('_add_', '+', False), other)

    def __sub__(self, other):
        return self._binary_op(('_sub_', '-', False), other)

    def __mul__(self, other):
        return self._binary_op(('_mul_', '*', False), other)

    def __truediv__(self, other):
        return self._binary_op(('_div_', '/', False), other)


class IndependentParameter(Parameter):
    DefaultRange = (-10, 10)

    def __init__(self, name, value, lo=None, hi=None):
        super(IndependentParameter, self).__init__(name, value)
        self._lo = lo if lo is not None else self.DefaultRange[0]
        self._hi = hi if hi is not None else self.DefaultRange[1]

    def renderRoofit(self, workspace):
        if workspace.var(self._name) == None:  # noqa: E711
            var = ROOT.RooRealVar(self._name, self._name, self.value, self._lo, self._hi)
            workspace.add(var)
        return workspace.var(self._name)


class NuisanceParameter(IndependentParameter):
    def __init__(self, name, combinePrior, value=0, lo=None, hi=None):
        '''
        A nuisance parameter.
        name: name of parameter
        combinePrior: one of 'shape', 'shapeN', 'lnN', etc.

        Render the prior somewhere else?  Probably in Model because the prior needs
        to be added at the RooSimultaneus level (I think)
        Filtering the set of model parameters for these classes can collect needed priors.
        '''
        super(NuisanceParameter, self).__init__(name, value, lo, hi)
        self._hasPrior = True
        self._prior = combinePrior

    # TODO: unused?
    def __str__(self):
        return "%s %s" % self.name, self.prior

    @property
    def combinePrior(self):
        return self._prior


class DependentParameter(Parameter):
    def __init__(self, name, formula, *dependents):
        '''
        Create a dependent parameter
            name: name of parameter
            formula: a python format-string using only indices, e.g.
                '{0} + sin({1})*{2}'
        '''
        super(DependentParameter, self).__init__(name, np.nan)
        if not all(isinstance(d, Parameter) for d in dependents):
            raise ValueError
        self._formula = formula
        self._dependents = dependents

    @property
    def value(self):
        # TODO: value from rendering formula and eval() or numexpr or TFormula or ...
        raise NotImplementedError

    @Parameter.intermediate.setter
    def intermediate(self, val):
        self._intermediate = val

    def getDependents(self, rendering=False):
        if not (self.intermediate or rendering):
            return {self}
        dependents = set()
        for p in self._dependents:
            if p.intermediate:
                dependents.update(p.getDependents())
            else:
                dependents.add(p)
        return dependents

    def formula(self, rendering=False):
        if not (self.intermediate or rendering):
            return "{" + self.name + "}"
        return "(" + self._formula.format(*(p.formula() for p in self._dependents)) + ")"

    def renderRoofit(self, workspace):
        if workspace.function(self._name) == None:  # noqa: E711
            if self.intermediate:
                # This is a warning because we should make sure the name does not conflict as
                # intermediate parameter names are often autogenerated and might not be unique/appropriate
                warnings.warn("Rendering intermediate parameter: %r" % self, RuntimeWarning)
                self.intermediate = False
            rooVars = [v.renderRoofit(workspace) for v in self.getDependents(rendering=True)]
            # Originally just passed the named variables to RooFormulaVar but it seems the TFormula class
            # is more sensitive to variable names than is reasonable, so we reindex here
            formula = self.formula(rendering=True).format(**{var.GetName(): '@%d' % i for i, var in enumerate(rooVars)})
            var = ROOT.RooFormulaVar(self._name, self._name, formula, ROOT.RooArgList.fromiter(rooVars))
            workspace.add(var)
        return workspace.function(self._name)