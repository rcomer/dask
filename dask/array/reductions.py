from __future__ import absolute_import, division, print_function

from functools import partial, wraps
from itertools import product
from math import factorial, log, ceil

import numpy as np
from toolz import compose, partition_all, merge, get

from . import chunk
from .core import _concatenate2, Array, atop, sqrt, lol_tuples
from .numpy_compat import divide
from ..compatibility import getargspec, builtins
from ..base import tokenize
from ..utils import ignoring


def reduction(x, chunk, aggregate, axis=None, keepdims=None, dtype=None,
              max_leaves=None, combine=None):
    """ General version of reductions

    >>> reduction(my_array, np.sum, np.sum, axis=0, keepdims=False)  # doctest: +SKIP
    """
    if axis is None:
        axis = tuple(range(x.ndim))
    if isinstance(axis, int):
        axis = (axis,)
    axis = tuple(i if i >= 0 else x.ndim + i for i in axis)

    if dtype and 'dtype' in getargspec(chunk).args:
        chunk = partial(chunk, dtype=dtype)
    if dtype and 'dtype' in getargspec(aggregate).args:
        aggregate = partial(aggregate, dtype=dtype)

    # Normalize axes
    if isinstance(max_leaves, dict):
        max_leaves = dict((k, max_leaves.get(k, 2)) for k in axis)
    elif isinstance(max_leaves, int):
        n = builtins.max(int(max_leaves ** (1/len(axis))), 2)
        max_leaves = dict.fromkeys(axis, n)
    else:
        max_leaves = dict((k, v) for (k, v) in enumerate(x.numblocks) if k in axis)

    # Map chunk across all blocks
    inds = tuple(range(x.ndim))
    tmp = atop(partial(chunk, axis=axis, keepdims=True), inds, x, inds)
    tmp._chunks = tuple((1,)*len(c) if i in axis else c for (i, c)
                        in enumerate(tmp.chunks))

    # Reduce across intermediates
    depth = 1
    for i, n in enumerate(tmp.numblocks):
        if i in max_leaves and max_leaves[i] != 1:
            depth = int(builtins.max(depth, ceil(log(n, max_leaves[i]))))
    func = compose(partial(combine or aggregate, axis=axis, keepdims=True),
                   partial(_concatenate2, axes=axis))
    for i in range(depth - 1):
        tmp = partial_reduce(func, tmp, max_leaves, True, None)
    func = compose(partial(aggregate, axis=axis, keepdims=keepdims),
                   partial(_concatenate2, axes=axis))
    return partial_reduce(func, tmp, max_leaves, keepdims=keepdims, dtype=dtype,
                          name=('reduce-' + tokenize(func, x, keepdims, dtype)))


def partial_reduce(func, x, max_leaves, keepdims=False, dtype=None, name=None):
    """Partial reduction across multiple axes.

    Parameters
    ----------
    func : function
    x : Array
    max_leaves : dict
        Maximum reduction block sizes in each dimension.

    Example
    -------
    Reduce across axis 0 and 2, merging a maximum of 1 block in the 0th
    dimension, and 3 blocks in the 2nd dimension:

    >>> partial_reduce(np.min, x, {0: 1, 2: 3})    # doctest: +SKIP
    """
    name = name or 'p_reduce-' + tokenize(func, x, max_leaves, keepdims, dtype)
    parts = [list(partition_all(max_leaves.get(i, 1), range(n))) for (i, n)
             in enumerate(x.numblocks)]
    keys = product(*map(range, map(len, parts)))
    out_chunks = [tuple(1 for p in partition_all(max_leaves[i], c)) if i
                  in max_leaves else c for (i, c) in enumerate(x.chunks)]
    if not keepdims:
        out_axis = [i for i in range(x.ndim) if i not in max_leaves]
        getter = lambda k: get(out_axis, k)
        keys = map(getter, keys)
        out_chunks = list(getter(out_chunks))
    dsk = {}
    for k, p in zip(keys, product(*parts)):
        decided = dict((i, j[0]) for (i, j) in enumerate(p) if len(j) == 1)
        dummy = dict(i for i in enumerate(p) if i[0] not in decided)
        g = lol_tuples((x.name,), range(x.ndim), decided, dummy)
        dsk[(name,) + k] = (func, g)
    return Array(merge(dsk, x.dask), name, out_chunks, dtype=dtype)


@wraps(chunk.sum)
def sum(a, axis=None, dtype=None, keepdims=False, max_leaves=None):
    if dtype is not None:
        dt = dtype
    elif a._dtype is not None:
        dt = np.empty((1,), dtype=a._dtype).sum().dtype
    else:
        dt = None
    return reduction(a, chunk.sum, chunk.sum, axis=axis, keepdims=keepdims,
                     dtype=dt, max_leaves=max_leaves)


@wraps(chunk.prod)
def prod(a, axis=None, dtype=None, keepdims=False, max_leaves=None):
    if dtype is not None:
        dt = dtype
    elif a._dtype is not None:
        dt = np.empty((1,), dtype=a._dtype).prod().dtype
    else:
        dt = None
    return reduction(a, chunk.prod, chunk.prod, axis=axis, keepdims=keepdims,
                     dtype=dt, max_leaves=max_leaves)


@wraps(chunk.min)
def min(a, axis=None, keepdims=False, max_leaves=None):
    return reduction(a, chunk.min, chunk.min, axis=axis, keepdims=keepdims,
                     dtype=a._dtype, max_leaves=max_leaves)


@wraps(chunk.max)
def max(a, axis=None, keepdims=False, max_leaves=None):
    return reduction(a, chunk.max, chunk.max, axis=axis, keepdims=keepdims,
                     dtype=a._dtype, max_leaves=max_leaves)


@wraps(chunk.any)
def any(a, axis=None, keepdims=False, max_leaves=None):
    return reduction(a, chunk.any, chunk.any, axis=axis, keepdims=keepdims,
                     dtype='bool', max_leaves=max_leaves)


@wraps(chunk.all)
def all(a, axis=None, keepdims=False, max_leaves=None):
    return reduction(a, chunk.all, chunk.all, axis=axis, keepdims=keepdims,
                     dtype='bool', max_leaves=max_leaves)


@wraps(chunk.nansum)
def nansum(a, axis=None, dtype=None, keepdims=False, max_leaves=None):
    if dtype is not None:
        dt = dtype
    elif a._dtype is not None:
        dt = chunk.nansum(np.empty((1,), dtype=a._dtype)).dtype
    else:
        dt = None
    return reduction(a, chunk.nansum, chunk.sum, axis=axis, keepdims=keepdims,
                     dtype=dt, max_leaves=max_leaves)


with ignoring(AttributeError):
    @wraps(chunk.nanprod)
    def nanprod(a, axis=None, dtype=None, keepdims=False, max_leaves=None):
        if dtype is not None:
            dt = dtype
        elif a._dtype is not None:
            dt = np.empty((1,), dtype=a._dtype).nanprod().dtype
        else:
            dt = None
        return reduction(a, chunk.nanprod, chunk.prod, axis=axis,
                         keepdims=keepdims, dtype=dt, max_leaves=max_leaves)


@wraps(chunk.nanmin)
def nanmin(a, axis=None, keepdims=False, max_leaves=None):
    return reduction(a, chunk.nanmin, chunk.nanmin, axis=axis,
                     keepdims=keepdims, dtype=a._dtype, max_leaves=max_leaves)


@wraps(chunk.nanmax)
def nanmax(a, axis=None, keepdims=False, max_leaves=None):
    return reduction(a, chunk.nanmax, chunk.nanmax, axis=axis,
                     keepdims=keepdims, dtype=a._dtype, max_leaves=max_leaves)


def numel(x, **kwargs):
    """ A reduction to count the number of elements """
    return chunk.sum(np.ones_like(x), **kwargs)


def nannumel(x, **kwargs):
    """ A reduction to count the number of elements """
    return chunk.sum(~np.isnan(x), **kwargs)


def mean_chunk(x, sum=chunk.sum, numel=numel, dtype='f8', **kwargs):
    n = numel(x, dtype=dtype, **kwargs)
    total = sum(x, dtype=dtype, **kwargs)
    result = np.empty(shape=n.shape,
              dtype=[('total', total.dtype), ('n', n.dtype)])
    result['n'] = n
    result['total'] = total
    return result


def mean_combine(pair, sum=chunk.sum, numel=numel, dtype='f8', **kwargs):
    n = sum(pair['n'], **kwargs)
    total = sum(pair['total'], **kwargs)
    result = np.empty(shape=n.shape, dtype=pair.dtype)
    result['n'] = n
    result['total'] = total
    return result


def mean_agg(pair, dtype='f8', **kwargs):
    return divide(pair['total'].sum(dtype=dtype, **kwargs),
                  pair['n'].sum(dtype=dtype, **kwargs), dtype=dtype)


@wraps(chunk.mean)
def mean(a, axis=None, dtype=None, keepdims=False, max_leaves=None):
    if dtype is not None:
        dt = dtype
    elif a._dtype is not None:
        dt = np.mean(np.empty(shape=(1,), dtype=a._dtype)).dtype
    else:
        dt = None
    return reduction(a, mean_chunk, mean_agg, axis=axis, keepdims=keepdims,
                     dtype=dt, max_leaves=max_leaves, combine=mean_combine)


def nanmean(a, axis=None, dtype=None, keepdims=False, max_leaves=None):
    if dtype is not None:
        dt = dtype
    elif a._dtype is not None:
        dt = np.mean(np.empty(shape=(1,), dtype=a._dtype)).dtype
    else:
        dt = None
    return reduction(a, partial(mean_chunk, sum=chunk.nansum, numel=nannumel),
                     mean_agg, axis=axis, keepdims=keepdims, dtype=dt,
                     max_leaves=max_leaves,
                     combine=partial(mean_combine, sum=chunk.nansum, numel=nannumel))

with ignoring(AttributeError):
    nanmean = wraps(chunk.nanmean)(nanmean)


def moment_chunk(A, order=2, sum=chunk.sum, numel=numel, dtype='f8', **kwargs):
    total = sum(A, dtype=dtype, **kwargs)
    n = numel(A, **kwargs)
    u = total/n
    M = np.empty(shape=n.shape + (order - 1,), dtype=dtype)
    for i in range(2, order + 1):
        M[..., i - 2] = sum((A - u)**i, dtype=dtype, **kwargs)
    result = np.empty(shape=n.shape, dtype=[('total', total.dtype),
                                            ('n', n.dtype),
                                            ('M', M.dtype, (order-1,))])
    result['total'] = total
    result['n'] = n
    result['M'] = M
    return result


def _moment_helper(Ms, ns, inner_term, order, sum, kwargs):
    M = Ms[..., order - 2].sum(**kwargs) + sum(ns * inner_term**order, **kwargs)
    for k in range(1, order - 1):
        coeff = factorial(order)/(factorial(k)*factorial(order - k))
        M += coeff * sum(Ms[..., order - k - 2] * inner_term**k, **kwargs)
    return M


def moment_combine(data, order=2, ddof=0, dtype='f8', sum=np.sum, **kwargs):
    kwargs['dtype'] = dtype
    kwargs['keepdims'] = True

    totals = data['total']
    ns = data['n']
    Ms = data['M']
    total = totals.sum(**kwargs)
    n = sum(ns, **kwargs)
    mu = divide(total, n, dtype=dtype)
    inner_term = divide(totals, ns, dtype=dtype) - mu
    M = np.empty(shape=n.shape + (order - 1,), dtype=dtype)

    for o in range(2, order + 1):
        M[..., o - 2] = _moment_helper(Ms, ns, inner_term, o, sum, kwargs)

    result = np.zeros(shape=n.shape, dtype=[('total', total.dtype),
                                            ('n', n.dtype),
                                            ('M', Ms.dtype, (order-1,))])
    result['total'] = total
    result['n'] = n
    result['M'] = M
    return result


def moment_agg(data, order=2, ddof=0, dtype='f8', sum=np.sum, **kwargs):
    totals = data['total']
    ns = data['n']
    Ms = data['M']

    kwargs['dtype'] = dtype
    # To properly handle ndarrays, the original dimensions need to be kept for
    # part of the calculation.
    keepdim_kw = kwargs.copy()
    keepdim_kw['keepdims'] = True

    n = sum(ns, **keepdim_kw)
    mu = divide(totals.sum(**keepdim_kw), n, dtype=dtype)
    inner_term = divide(totals, ns, dtype=dtype) - mu

    M = _moment_helper(Ms, ns, inner_term, order, sum, kwargs)
    return divide(M, sum(n, **kwargs) - ddof, dtype=dtype)


def moment(a, order, axis=None, dtype=None, keepdims=False, ddof=0,
           max_leaves=None):
    if not isinstance(order, int) or order < 2:
        raise ValueError("Order must be an integer >= 2")
    if dtype is not None:
        dt = dtype
    elif a._dtype is not None:
        dt = np.var(np.ones(shape=(1,), dtype=a._dtype)).dtype
    else:
        dt = None
    return reduction(a, partial(moment_chunk, order=order), partial(moment_agg,
                     order=order, ddof=ddof), axis=axis, keepdims=keepdims,
                     dtype=dt, max_leaves=max_leaves,
                     combine=partial(moment_combine, order=order))


@wraps(chunk.var)
def var(a, axis=None, dtype=None, keepdims=False, ddof=0, max_leaves=None):
    if dtype is not None:
        dt = dtype
    elif a._dtype is not None:
        dt = np.var(np.ones(shape=(1,), dtype=a._dtype)).dtype
    else:
        dt = None
    return reduction(a, moment_chunk, partial(moment_agg, ddof=ddof), axis=axis,
                     keepdims=keepdims, dtype=dt, max_leaves=max_leaves,
                     combine=moment_combine)


def nanvar(a, axis=None, dtype=None, keepdims=False, ddof=0, max_leaves=None):
    if dtype is not None:
        dt = dtype
    elif a._dtype is not None:
        dt = np.var(np.ones(shape=(1,), dtype=a._dtype)).dtype
    else:
        dt = None
    return reduction(a, partial(moment_chunk, sum=chunk.nansum, numel=nannumel),
                     partial(moment_agg, sum=np.nansum, ddof=ddof), axis=axis,
                     keepdims=keepdims, dtype=dt, max_leaves=max_leaves,
                     combine=partial(moment_combine, sum=np.nansum))

with ignoring(AttributeError):
    nanvar = wraps(chunk.nanvar)(nanvar)

@wraps(chunk.std)
def std(a, axis=None, dtype=None, keepdims=False, ddof=0, max_leaves=None):
    result = sqrt(a.var(axis=axis, dtype=dtype, keepdims=keepdims, ddof=ddof,
                        max_leaves=max_leaves))
    if dtype and dtype != result.dtype:
        result = result.astype(dtype)
    return result


def nanstd(a, axis=None, dtype=None, keepdims=False, ddof=0, max_leaves=None):
    result = sqrt(nanvar(a, axis=axis, dtype=dtype, keepdims=keepdims,
                         ddof=ddof, max_leaves=max_leaves))
    if dtype and dtype != result.dtype:
        result = result.astype(dtype)
    return result

with ignoring(AttributeError):
    nanstd = wraps(chunk.nanstd)(nanstd)


def vnorm(a, ord=None, axis=None, dtype=None, keepdims=False, max_leaves=None):
    """ Vector norm

    See np.linalg.norm
    """
    if ord is None or ord == 'fro':
        ord = 2
    if ord == np.inf:
        return max(abs(a), axis=axis, keepdims=keepdims, max_leaves=max_leaves)
    elif ord == -np.inf:
        return min(abs(a), axis=axis, keepdims=keepdims, max_leaves=max_leaves)
    elif ord == 1:
        return sum(abs(a), axis=axis, dtype=dtype, keepdims=keepdims,
                   max_leaves=max_leaves)
    elif ord % 2 == 0:
        return sum(a**ord, axis=axis, dtype=dtype, keepdims=keepdims,
                   max_leaves=max_leaves)**(1./ord)
    else:
        return sum(abs(a)**ord, axis=axis, dtype=dtype, keepdims=keepdims,
                   max_leaves=max_leaves)**(1./ord)


def _arg_combine(data, axis, argfunc):
    """Merge intermediate results from ``arg_*`` functions"""
    vals = data['vals']
    arg = data['arg']
    ns = data['n']
    args = argfunc(vals, axis=axis)
    offsets = np.roll(np.cumsum(ns, axis=axis), 1, axis)
    offsets[tuple(slice(None) if i != axis else 0 for i in range(ns.ndim))] = 0
    inds = list(reversed(np.meshgrid(*map(np.arange, args.shape), sparse=True)))
    inds.insert(axis, args)

    arg = (arg + offsets)[tuple(inds)]
    vals = vals[tuple(inds)]
    n = ns.sum(axis=axis).take(0, 0)
    return arg, vals, n


def arg_chunk(func, argfunc, x, axis=None, **kwargs):
    axis = axis[0] if isinstance(axis, tuple) else axis
    vals = func(x, axis=axis, keepdims=True)
    arg = argfunc(x, axis=axis, keepdims=True)
    result = np.empty(shape=vals.shape, dtype=[('vals', vals.dtype),
                                               ('arg', arg.dtype),
                                               ('n', 'i8')])
    result['vals'] = vals
    result['arg'] = arg
    result['n'] = x.shape[axis]
    return result


def arg_combine(func, argfunc, data, axis=None, **kwargs):
    axis = axis[0] if isinstance(axis, tuple) else axis
    arg, vals, n = _arg_combine(data, axis, argfunc)
    shape = tuple(s if i != axis else 1 for (i, s) in enumerate(data.shape))
    result = np.empty(shape=shape, dtype=[('vals', vals.dtype),
                                          ('arg', arg.dtype),
                                          ('n', 'i8')])
    result['vals'] = vals.reshape(shape)
    result['arg'] = arg.reshape(shape)
    result['n'] = n
    return result


def arg_agg(func, argfunc, data, axis=None, **kwargs):
    axis = axis[0] if isinstance(axis, tuple) else axis
    return _arg_combine(data, axis, argfunc)[0]


def arg_reduction(func, argfunc):
    chunk = partial(arg_chunk, func, argfunc)
    agg = partial(arg_agg, func, argfunc)
    combine = partial(arg_combine, func, argfunc)
    @wraps(argfunc)
    def _(a, axis=None, max_leaves=None):
        if axis < 0:
            axis = a.ndim + axis
        return reduction(a, chunk, agg, axis=axis, dtype='i8',
                         max_leaves=max_leaves, combine=combine)
    return _


argmin = arg_reduction(chunk.min, chunk.argmin)
argmax = arg_reduction(chunk.max, chunk.argmax)
nanargmin = arg_reduction(chunk.nanmin, chunk.nanargmin)
nanargmax = arg_reduction(chunk.nanmax, chunk.nanargmax)
