# Copyright (c) 2017-present, Facebook, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##############################################################################
#
# Based on:
# --------------------------------------------------------
# Fast/er R-CNN
# Licensed under The MIT License [see LICENSE for details]
# Written by Ross Girshick
# --------------------------------------------------------

"""Box manipulation functions. The internal Detectron box format is
[x1, y1, x2, y2] where (x1, y1) specify the top-left box corner and (x2, y2)
specify the bottom-right box corner. Boxes from external sources, e.g.,
datasets, may be in other formats (such as [x, y, w, h]) and require conversion.

This module uses a convention that may seem strange at first: the width of a box
is computed as x2 - x1 + 1 (likewise for height). The "+ 1" dates back to old
object detection days when the coordinates were integer pixel indices, rather
than floating point coordinates in a subpixel coordinate frame. A box with x2 =
x1 and y2 = y1 was taken to include a single pixel, having a width of 1, and
hence requiring the "+ 1". Now, most datasets will likely provide boxes with
floating point coordinates and the width should be more reasonably computed as
x2 - x1.

In practice, as long as a model is trained and tested with a consistent
convention either decision seems to be ok (at least in our experience on COCO).
Since we have a long history of training models with the "+ 1" convention, we
are reluctant to change it even if our modern tastes prefer not to use it.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import numpy as np

from detectron.core.config import cfg
import detectron.utils.cython_bbox as cython_bbox
import detectron.utils.cython_nms as cython_nms
import detectron.utils.cython_polygon_nms as cython_polygon_nms

bbox_overlaps = cython_bbox.bbox_overlaps


def boxes_area(boxes):
    """Compute the area of an array of boxes."""
    w = (boxes[:, 2] - boxes[:, 0] + 1)
    h = (boxes[:, 3] - boxes[:, 1] + 1)
    areas = w * h
    assert np.all(areas >= 0), 'Negative areas founds'
    return areas


def unique_boxes(boxes, scale=1.0):
    """Return indices of unique boxes."""
    v = np.array([1, 1e3, 1e6, 1e9])
    hashes = np.round(boxes * scale).dot(v)
    _, index = np.unique(hashes, return_index=True)
    return np.sort(index)


def xywh_to_xyxy(xywh):
    """Convert [x1 y1 w h] box format to [x1 y1 x2 y2] format."""
    if isinstance(xywh, (list, tuple)):
        # Single box given as a list of coordinates
        assert len(xywh) == 4
        x1, y1 = xywh[0], xywh[1]
        x2 = x1 + np.maximum(0., xywh[2] - 1.)
        y2 = y1 + np.maximum(0., xywh[3] - 1.)
        return (x1, y1, x2, y2)
    elif isinstance(xywh, np.ndarray):
        # Multiple boxes given as a 2D ndarray
        return np.hstack(
            (xywh[:, 0:2], xywh[:, 0:2] + np.maximum(0, xywh[:, 2:4] - 1))
        )
    else:
        raise TypeError('Argument xywh must be a list, tuple, or numpy array.')


def xyxy_to_xywh(xyxy):
    """Convert [x1 y1 x2 y2] box format to [x1 y1 w h] format."""
    if isinstance(xyxy, (list, tuple)):
        # Single box given as a list of coordinates
        assert len(xyxy) == 4
        x1, y1 = xyxy[0], xyxy[1]
        w = xyxy[2] - x1 + 1
        h = xyxy[3] - y1 + 1
        return (x1, y1, w, h)
    elif isinstance(xyxy, np.ndarray):
        # Multiple boxes given as a 2D ndarray
        return np.hstack((xyxy[:, 0:2], xyxy[:, 2:4] - xyxy[:, 0:2] + 1))
    else:
        raise TypeError('Argument xyxy must be a list, tuple, or numpy array.')


def filter_small_boxes(boxes, min_size):
    """Keep boxes with width and height both greater than min_size."""
    w = boxes[:, 2] - boxes[:, 0] + 1
    h = boxes[:, 3] - boxes[:, 1] + 1
    keep = np.where((w > min_size) & (h > min_size))[0]
    return keep


def clip_boxes_to_image(boxes, height, width):
    """Clip an array of boxes to an image with the given height and width."""
    boxes[:, [0, 2]] = np.minimum(width - 1., np.maximum(0., boxes[:, [0, 2]]))
    boxes[:, [1, 3]] = np.minimum(height - 1., np.maximum(0., boxes[:, [1, 3]]))
    return boxes


def clip_xyxy_to_image(x1, y1, x2, y2, height, width):
    """Clip coordinates to an image with the given height and width."""
    x1 = np.minimum(width - 1., np.maximum(0., x1))
    y1 = np.minimum(height - 1., np.maximum(0., y1))
    x2 = np.minimum(width - 1., np.maximum(0., x2))
    y2 = np.minimum(height - 1., np.maximum(0., y2))
    return x1, y1, x2, y2


def clip_tiled_boxes(boxes, im_shape):
    """Clip boxes to image boundaries. im_shape is [height, width] and boxes
    has shape (N, 4 * num_tiled_boxes)."""
    assert boxes.shape[1] % 4 == 0, \
        'boxes.shape[1] is {:d}, but must be divisible by 4.'.format(
        boxes.shape[1]
    )
    # x1 >= 0
    boxes[:, 0::4] = np.maximum(np.minimum(boxes[:, 0::4], im_shape[1] - 1), 0)
    # y1 >= 0
    boxes[:, 1::4] = np.maximum(np.minimum(boxes[:, 1::4], im_shape[0] - 1), 0)
    # x2 < im_shape[1]
    boxes[:, 2::4] = np.maximum(np.minimum(boxes[:, 2::4], im_shape[1] - 1), 0)
    # y2 < im_shape[0]
    boxes[:, 3::4] = np.maximum(np.minimum(boxes[:, 3::4], im_shape[0] - 1), 0)
    return boxes

def clip_tiled_polygons(polygons, im_shape):
    """Clip boxes to image boundaries. im_shape is [height, width] and polygons
    has shape (N, 8 * num_tiled_boxes)."""
    assert polygons.shape[1] % 8 == 0, \
        'polygons.shape[1] is {:d}, but must be divisible by 8.'.format(
        polygons.shape[1]
    )
    # x1 >= 0 && < im_shape[1]
    polygons[:, 0::8] = np.maximum(np.minimum(polygons[:, 0::8], im_shape[1] - 1), 0)
    # y1 >= 0 && < im_shape[0]
    polygons[:, 1::8] = np.maximum(np.minimum(polygons[:, 1::8], im_shape[0] - 1), 0)
    # x2 >= 0 && < im_shape[1]
    polygons[:, 2::8] = np.maximum(np.minimum(polygons[:, 2::8], im_shape[1] - 1), 0)
    # y2 >=0 && < im_shape[0]
    polygons[:, 3::8] = np.maximum(np.minimum(polygons[:, 3::8], im_shape[0] - 1), 0)
    # x3 >= 0 && < im_shape[1]
    polygons[:, 4::8] = np.maximum(np.minimum(polygons[:, 4::8], im_shape[1] - 1), 0)
    # y3 >= 0 && < im_shape[0]
    polygons[:, 5::8] = np.maximum(np.minimum(polygons[:, 5::8], im_shape[0] - 1), 0)
    # x4 >= 0 && < im_shape[1]
    polygons[:, 6::8] = np.maximum(np.minimum(polygons[:, 6::8], im_shape[1] - 1), 0)
    # y4 >=0 && < im_shape[0]
    polygons[:, 7::8] = np.maximum(np.minimum(polygons[:, 7::8], im_shape[0] - 1), 0)
    return polygons


def polygon_transform(boxes, deltas, weights=(1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)):
    """Forward transform that maps proposal polygons to predicted ground-truth
    polygons using polygon regression deltas. See polygon_transform_inv for a
    description of the weights argument.
    """
    if boxes.shape[0] == 0:
        return np.zeros((0, deltas.shape[1]), dtype=deltas.dtype)

        boxes = boxes.astype(deltas.dtype, copy=False)

    widths = boxes[:, 2] - boxes[:, 0] + 1.0
    heights = boxes[:, 3] - boxes[:, 1] + 1.0
    ctr_x = boxes[:, 0] + 0.5 * widths
    ctr_y = boxes[:, 1] + 0.5 * heights

    wx1, wy1, wx2, wy2, wx3, wy3, wx4, wy4 = weights
    dx1 = deltas[:, 0::8] / wx1
    dy1 = deltas[:, 1::8] / wy1
    dx2 = deltas[:, 2::8] / wx2
    dy2 = deltas[:, 3::8] / wy2
    dx3 = deltas[:, 4::8] / wx3
    dy3 = deltas[:, 5::8] / wy3
    dx4 = deltas[:, 6::8] / wx4
    dy4 = deltas[:, 7::8] / wy4

    pred_polygons = np.zeros(deltas.shape, dtype=deltas.dtype)
    # x1
    pred_polygons[:, 0::8] = dx1 * widths[:, np.newaxis] + ctr_x[:, np.newaxis]
    # y1
    pred_polygons[:, 1::8] = dy1 * heights[:, np.newaxis] + ctr_y[:, np.newaxis]
    # x2
    pred_polygons[:, 2::8] = dx2 * widths[:, np.newaxis] + ctr_x[:, np.newaxis]
    # y2
    pred_polygons[:, 3::8] = dy2 * heights[:, np.newaxis] + ctr_y[:, np.newaxis]
    # x3
    pred_polygons[:, 4::8] = dx3 * widths[:, np.newaxis] + ctr_x[:, np.newaxis]
    # y3
    pred_polygons[:, 5::8] = dy3 * heights[:, np.newaxis] + ctr_y[:, np.newaxis]
    # x4
    pred_polygons[:, 6::8] = dx4 * widths[:, np.newaxis] + ctr_x[:, np.newaxis]
    # y4
    pred_polygons[:, 7::8] = dy4 * heights[:, np.newaxis] + ctr_y[:, np.newaxis]


    return pred_polygons


def bbox_transform(boxes, deltas, weights=(1.0, 1.0, 1.0, 1.0)):
    """Forward transform that maps proposal boxes to predicted ground-truth
    boxes using bounding-box regression deltas. See bbox_transform_inv for a
    description of the weights argument.
    """
    if boxes.shape[0] == 0:
        return np.zeros((0, deltas.shape[1]), dtype=deltas.dtype)

    boxes = boxes.astype(deltas.dtype, copy=False)

    widths = boxes[:, 2] - boxes[:, 0] + 1.0
    heights = boxes[:, 3] - boxes[:, 1] + 1.0
    ctr_x = boxes[:, 0] + 0.5 * widths
    ctr_y = boxes[:, 1] + 0.5 * heights

    wx, wy, ww, wh = weights
    dx = deltas[:, 0::4] / wx
    dy = deltas[:, 1::4] / wy
    dw = deltas[:, 2::4] / ww
    dh = deltas[:, 3::4] / wh

    # Prevent sending too large values into np.exp()
    dw = np.minimum(dw, cfg.BBOX_XFORM_CLIP)
    dh = np.minimum(dh, cfg.BBOX_XFORM_CLIP)

    pred_ctr_x = dx * widths[:, np.newaxis] + ctr_x[:, np.newaxis]
    pred_ctr_y = dy * heights[:, np.newaxis] + ctr_y[:, np.newaxis]
    pred_w = np.exp(dw) * widths[:, np.newaxis]
    pred_h = np.exp(dh) * heights[:, np.newaxis]

    pred_boxes = np.zeros(deltas.shape, dtype=deltas.dtype)
    # x1
    pred_boxes[:, 0::4] = pred_ctr_x - 0.5 * pred_w
    # y1
    pred_boxes[:, 1::4] = pred_ctr_y - 0.5 * pred_h
    # x2 (note: "- 1" is correct; don't be fooled by the asymmetry)
    pred_boxes[:, 2::4] = pred_ctr_x + 0.5 * pred_w - 1
    # y2 (note: "- 1" is correct; don't be fooled by the asymmetry)
    pred_boxes[:, 3::4] = pred_ctr_y + 0.5 * pred_h - 1

    return pred_boxes


def bbox_transform_inv(boxes, gt_boxes, weights=(1.0, 1.0, 1.0, 1.0)):
    """Inverse transform that computes target bounding-box regression deltas
    given proposal boxes and ground-truth boxes. The weights argument should be
    a 4-tuple of multiplicative weights that are applied to the regression
    target.

    In older versions of this code (and in py-faster-rcnn), the weights were set
    such that the regression deltas would have unit standard deviation on the
    training dataset. Presently, rather than computing these statistics exactly,
    we use a fixed set of weights (10., 10., 5., 5.) by default. These are
    approximately the weights one would get from COCO using the previous unit
    stdev heuristic.
    """
    ex_widths = boxes[:, 2] - boxes[:, 0] + 1.0
    ex_heights = boxes[:, 3] - boxes[:, 1] + 1.0
    ex_ctr_x = boxes[:, 0] + 0.5 * ex_widths
    ex_ctr_y = boxes[:, 1] + 0.5 * ex_heights

    gt_widths = gt_boxes[:, 2] - gt_boxes[:, 0] + 1.0
    gt_heights = gt_boxes[:, 3] - gt_boxes[:, 1] + 1.0
    gt_ctr_x = gt_boxes[:, 0] + 0.5 * gt_widths
    gt_ctr_y = gt_boxes[:, 1] + 0.5 * gt_heights

    wx, wy, ww, wh = weights
    targets_dx = wx * (gt_ctr_x - ex_ctr_x) / ex_widths
    targets_dy = wy * (gt_ctr_y - ex_ctr_y) / ex_heights
    targets_dw = ww * np.log(gt_widths / ex_widths)
    targets_dh = wh * np.log(gt_heights / ex_heights)

    targets = np.vstack((targets_dx, targets_dy, targets_dw,
                         targets_dh)).transpose()
    return targets

def polygon_transform_inv(boxes, gt_boxes, gt_segms, weights=(1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)):
    """Inverse transform that computes target bounding-box regression deltas
    given proposal boxes and ground-truth boxes. The weights argument should be
    a 4-tuple of multiplicative weights that are applied to the regression
    target.

    In older versions of this code (and in py-faster-rcnn), the weights were set
    such that the regression deltas would have unit standard deviation on the
    training dataset. Presently, rather than computing these statistics exactly,
    we use a fixed set of weights (10., 10., 5., 5.) by default. These are
    approximately the weights one would get from COCO using the previous unit
    stdev heuristic.
    """
    ex_widths = boxes[:, 2] - boxes[:, 0] + 1.0
    ex_heights = boxes[:, 3] - boxes[:, 1] + 1.0
    ex_ctr_x = boxes[:, 0] + 0.5 * ex_widths
    ex_ctr_y = boxes[:, 1] + 0.5 * ex_heights

    gt_widths = gt_boxes[:, 2] - gt_boxes[:, 0] + 1.0
    gt_heights = gt_boxes[:, 3] - gt_boxes[:, 1] + 1.0
    gt_ctr_x = gt_boxes[:, 0] + 0.5 * gt_widths
    gt_ctr_y = gt_boxes[:, 1] + 0.5 * gt_heights

    gt_ctr_x1 = gt_boxes[:, 0]
    gt_ctr_y1 = gt_boxes[:, 1]
    gt_ctr_x2 = gt_boxes[:, 0] + gt_widths
    gt_ctr_y2 = gt_boxes[:, 1]
    gt_ctr_x3 = gt_boxes[:, 0] + gt_widths
    gt_ctr_y3 = gt_boxes[:, 1] + gt_heights
    gt_ctr_x4 = gt_boxes[:, 0]
    gt_ctr_y4 = gt_boxes[:, 1] + gt_heights

    wx1, wy1, wx2, wy2, wx3, wy3, wx4, wy4 = weights
    '''
    targets_dx1 = wx1 * (gt_segms[:, 0, 0] - ex_ctr_x) / ex_widths
    targets_dy1 = wy1 * (gt_segms[:, 0, 1] - ex_ctr_y) / ex_heights
    targets_dx2 = wx2 * (gt_segms[:, 1, 0] - ex_ctr_x) / ex_widths
    targets_dy2 = wy2 * (gt_segms[:, 1, 1] - ex_ctr_y) / ex_heights
    targets_dx3 = wx3 * (gt_segms[:, 2, 0] - ex_ctr_x) / ex_widths
    targets_dy3 = wy3 * (gt_segms[:, 2, 1] - ex_ctr_y) / ex_heights
    targets_dx4 = wx4 * (gt_segms[:, 3, 0] - ex_ctr_x) / ex_widths
    targets_dy4 = wy4 * (gt_segms[:, 3, 1] - ex_ctr_y) / ex_heights
    '''
    targets_dx1 = wx1 * (gt_ctr_x1 - ex_ctr_x) / ex_widths
    targets_dy1 = wy1 * (gt_ctr_y1 - ex_ctr_y) / ex_heights
    targets_dx2 = wx2 * (gt_ctr_x2 - ex_ctr_x) / ex_widths
    targets_dy2 = wy2 * (gt_ctr_y2 - ex_ctr_y) / ex_heights
    targets_dx3 = wx3 * (gt_ctr_x3 - ex_ctr_x) / ex_widths
    targets_dy3 = wy3 * (gt_ctr_y3 - ex_ctr_y) / ex_heights
    targets_dx4 = wx4 * (gt_ctr_x4 - ex_ctr_x) / ex_widths
    targets_dy4 = wy4 * (gt_ctr_y4 - ex_ctr_y) / ex_heights

    targets = np.vstack((targets_dx1, targets_dy1, targets_dx2, targets_dy2, targets_dx3, targets_dy3, targets_dx4, targets_dy4)).transpose()
    return targets


def expand_boxes(boxes, scale):
    """Expand an array of boxes by a given scale."""
    w_half = (boxes[:, 2] - boxes[:, 0]) * .5
    h_half = (boxes[:, 3] - boxes[:, 1]) * .5
    x_c = (boxes[:, 2] + boxes[:, 0]) * .5
    y_c = (boxes[:, 3] + boxes[:, 1]) * .5

    w_half *= scale
    h_half *= scale

    boxes_exp = np.zeros(boxes.shape)
    boxes_exp[:, 0] = x_c - w_half
    boxes_exp[:, 2] = x_c + w_half
    boxes_exp[:, 1] = y_c - h_half
    boxes_exp[:, 3] = y_c + h_half

    return boxes_exp


def flip_boxes(boxes, im_width):
    """Flip boxes horizontally."""
    boxes_flipped = boxes.copy()
    boxes_flipped[:, 0::4] = im_width - boxes[:, 2::4] - 1
    boxes_flipped[:, 2::4] = im_width - boxes[:, 0::4] - 1
    return boxes_flipped


def aspect_ratio(boxes, aspect_ratio):
    """Perform width-relative aspect ratio transformation."""
    boxes_ar = boxes.copy()
    boxes_ar[:, 0::4] = aspect_ratio * boxes[:, 0::4]
    boxes_ar[:, 2::4] = aspect_ratio * boxes[:, 2::4]
    return boxes_ar


def box_voting(top_dets, all_dets, thresh, scoring_method='ID', beta=1.0):
    """Apply bounding-box voting to refine `top_dets` by voting with `all_dets`.
    See: https://arxiv.org/abs/1505.01749. Optional score averaging (not in the
    referenced  paper) can be applied by setting `scoring_method` appropriately.
    """
    # top_dets is [N, 5] each row is [x1 y1 x2 y2, sore]
    # all_dets is [N, 5] each row is [x1 y1 x2 y2, sore]
    top_dets_out = top_dets.copy()
    top_boxes = top_dets[:, :4]
    all_boxes = all_dets[:, :4]
    all_scores = all_dets[:, 4]
    top_to_all_overlaps = bbox_overlaps(top_boxes, all_boxes)
    for k in range(top_dets_out.shape[0]):
        inds_to_vote = np.where(top_to_all_overlaps[k] >= thresh)[0]
        boxes_to_vote = all_boxes[inds_to_vote, :]
        ws = all_scores[inds_to_vote]
        top_dets_out[k, :4] = np.average(boxes_to_vote, axis=0, weights=ws)
        if scoring_method == 'ID':
            # Identity, nothing to do
            pass
        elif scoring_method == 'TEMP_AVG':
            # Average probabilities (considered as P(detected class) vs.
            # P(not the detected class)) after smoothing with a temperature
            # hyperparameter.
            P = np.vstack((ws, 1.0 - ws))
            P_max = np.max(P, axis=0)
            X = np.log(P / P_max)
            X_exp = np.exp(X / beta)
            P_temp = X_exp / np.sum(X_exp, axis=0)
            P_avg = P_temp[0].mean()
            top_dets_out[k, 4] = P_avg
        elif scoring_method == 'AVG':
            # Combine new probs from overlapping boxes
            top_dets_out[k, 4] = ws.mean()
        elif scoring_method == 'IOU_AVG':
            P = ws
            ws = top_to_all_overlaps[k, inds_to_vote]
            P_avg = np.average(P, weights=ws)
            top_dets_out[k, 4] = P_avg
        elif scoring_method == 'GENERALIZED_AVG':
            P_avg = np.mean(ws**beta)**(1.0 / beta)
            top_dets_out[k, 4] = P_avg
        elif scoring_method == 'QUASI_SUM':
            top_dets_out[k, 4] = ws.sum() / float(len(ws))**beta
        else:
            raise NotImplementedError(
                'Unknown scoring method {}'.format(scoring_method)
            )

    return top_dets_out


def polygon_nms(dets, thresh):
    """Apply classic DPM-style greedy NMS."""
    if dets.shape[0] == 0:
        return []
    return cython_polygon_nms.nms(dets, thresh)


def nms(dets, thresh):
    """Apply classic DPM-style greedy NMS."""
    if dets.shape[0] == 0:
        return []
    return cython_nms.nms(dets, thresh)


def soft_nms(
    dets, sigma=0.5, overlap_thresh=0.3, score_thresh=0.001, method='linear'
):
    """Apply the soft NMS algorithm from https://arxiv.org/abs/1704.04503."""
    if dets.shape[0] == 0:
        return dets, []

    methods = {'hard': 0, 'linear': 1, 'gaussian': 2}
    assert method in methods, 'Unknown soft_nms method: {}'.format(method)

    dets, keep = cython_nms.soft_nms(
        np.ascontiguousarray(dets, dtype=np.float32),
        np.float32(sigma),
        np.float32(overlap_thresh),
        np.float32(score_thresh),
        np.uint8(methods[method])
    )
    return dets, keep
