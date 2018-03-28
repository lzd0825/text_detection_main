# -*- coding:utf-8 -*-
import numpy as np
import numpy.random as npr
from .generate_anchors import generate_anchors
from .iou import bbox_overlaps


def anchor_target_layer(cfg, rpn_cls_score, gt_boxes, im_info, _feat_stride=(16,)):

    # 生成基本的anchor,一共10个,返回一个10行4列矩阵，每行为一个anchor，返回的只是基于中心的相对坐标
    _anchors = generate_anchors()
    _num_anchors = _anchors.shape[0]  # 10个anchor

    # allow boxes to sit over the edge by a small amount
    _allowed_border = 0

    # 第一张图片的im_info为[[800, 600, 1]]，所以要有下面这句话
    im_info = im_info[0]  # 图像的高宽及通道数

    assert rpn_cls_score.shape[0] == 1, 'Only single item batches are supported'

    # map of shape (..., H, W)
    height, width = rpn_cls_score.shape[1:3]  # feature-map的高宽

    # ==================================================================================
    shift_x = np.arange(0, width) * _feat_stride  # 返回一个列表，[0, 16, 32, 48, ...]
    shift_y = np.arange(0, height) * _feat_stride

    # 此时，shift_x作为一个行向量往下复制， 复制的次数等于shift_y的长度
    # 而shift_y作为一个列向量朝右复制，复制的次数等于shift_x的长度。这样他们的维度完全相同
    shift_x, shift_y = np.meshgrid(shift_x, shift_y) # in W H order
    # K is H x W
    # .ravel()将数组按行展开，展开为一行
    # .vstack()将四个展开列的以为数组垂直堆叠起来，再转置
    # shift的行数为像素个数，列数为4
    shifts = np.vstack((shift_x.ravel(), shift_y.ravel(),
                        shift_x.ravel(), shift_y.ravel())).transpose()
    # add A anchors (1, A, 4) to
    # cell K shifts (K, 1, 4) to get
    # shift anchors (K, A, 4)
    # reshape to (K*A, 4) shifted anchors
    A = _num_anchors  # 10个anchor
    K = shifts.shape[0]  # feature-map的像素个数

    # 前者的shape为(1, 10, 4), 后者的shape为(像素数, 1, 4)两者相加
    # 结果为(像素数, 10, 4) python数组广播相加。。。。。。。有待理解
    all_anchors = (_anchors.reshape((1, A, cfg.TRAIN.COORDINAE_NUM)) +
                   shifts.reshape((1, K, cfg.TRAIN.COORDINAE_NUM)).transpose((1, 0, 2)))

    # 至此，每一行为一个anchor， 每十行为一个滑动窗对应的十个anchor，第二个十行为往右走所对应的十个anchors
    all_anchors = all_anchors.reshape((K * A, cfg.TRAIN.COORDINAE_NUM))
    total_anchors = int(K * A)

    # 仅保留那些还在图像内部的anchor
    inds_inside = np.where(
        (all_anchors[:, 0] >= -_allowed_border) &
        (all_anchors[:, 1] >= -_allowed_border) &
        (all_anchors[:, 2] < im_info[1] + _allowed_border) &  # width
        (all_anchors[:, 3] < im_info[0] + _allowed_border)    # height
    )[0]
    total_valid_anchors = len(inds_inside)  # 在图片里面的anchors

    # 经过验证，这里的anchors的宽度全部是16
    anchors = all_anchors[inds_inside, :]  # 保留那些在图像内的anchor

    # 至此，anchor准备好了
    # ===============================================================================
    # label: 1 is positive, 0 is negative, -1 is dont care
    # (A)
    labels = np.empty((total_valid_anchors, ), dtype=np.int8)
    labels.fill(-1)  # 初始化label，均为-1

    # 计算anchor和gt-box的overlap，用来给anchor上标签
    overlaps = bbox_overlaps(
        np.ascontiguousarray(anchors, dtype=np.float),
        np.ascontiguousarray(gt_boxes, dtype=np.float))  # 假设anchors有x个，gt_boxes有y个，返回的是一个（x,y）的数组

    # argmax_overlaps[0]表示第0号anchor与所有GT的IOU最大值的脚标
    argmax_overlaps = overlaps.argmax(axis=1)

    # 返回一个一维数组，第i号元素的值表示第i个anchor与最可能的GT之间的IOU
    max_overlaps = overlaps[np.arange(len(inds_inside)), argmax_overlaps]

    # gt_argmax_overlaps[0]表示所有anchor中与第0号GT的IOU最大的那个anchor
    gt_argmax_overlaps = overlaps.argmax(axis=0)

    # 返回一个一维数组，第i号元素的值表示第i个GT与所有anchor的IOU最大的那个值
    gt_max_overlaps = overlaps[gt_argmax_overlaps,
                               np.arange(overlaps.shape[1])]

    #  这里[2, 2, 4, 5]表示2号anchor与所有的GT有两个最大值， 4号anchor与所有的GT有一个最大值
    #  这里的最大值，是指定一个GT后，与所有anchor的最大值
    gt_argmax_overlaps = np.where((overlaps - gt_max_overlaps) < 1e-10)[0]

    # 最大iou < 0.3 的设置为负例
    labels[max_overlaps < cfg.TRAIN.RPN_NEGATIVE_OVERLAP] = 0

    # 由于所有的anchors是穷举扫描，覆盖了全部的图片，对于某个GT，与其有最大的IOU的一定是文字
    labels[gt_argmax_overlaps] = 1

    # cfg.TRAIN.RPN_POSITIVE_OVERLAP = 0.7
    labels[max_overlaps >= cfg.TRAIN.RPN_POSITIVE_OVERLAP] = 1  # overlap大于0.7的认为是前景

    # TODO 限制正样本的数量不超过150个
    # TODO 这个后期可能还需要修改，毕竟如果使用的是字符的片段，那个正样本的数量是很多的。
    num_fg = int(cfg.TRAIN.RPN_FG_FRACTION * cfg.TRAIN.RPN_BATCHSIZE)  # 0.5*300
    fg_inds = np.where(labels == 1)[0]
    if len(fg_inds) > num_fg:
        disable_inds = npr.choice(
            fg_inds, size=(len(fg_inds) - num_fg), replace=False)  # 随机去除掉一些正样本
        labels[disable_inds] = -1  # 变为-1
    else:
        print("warning: The number of positive anchors is {}".format(len(fg_inds)))
        print("maybe you should adjust the value of cfg.TRAIN.RPN_FG_FRACTION")

    # subsample negative labels if we have too many
    # 对负样本进行采样，如果负样本的数量太多的话
    # 正负样本总数是300，限制正样本数目最多150，
    num_bg = cfg.TRAIN.RPN_BATCHSIZE - np.sum(labels == 1)
    bg_inds = np.where(labels == 0)[0]
    if len(bg_inds) > num_bg:
        disable_inds = npr.choice(
            bg_inds, size=(len(bg_inds) - num_bg), replace=False)
        labels[disable_inds] = -1
    else:
        print("warning: The number of negtive anchors is {}".format(len(fg_inds)))
        print("maybe you should adjust the value of cfg.TRAIN.RPN_BATCHSIZE")

    # 至此， 上好标签，开始计算rpn-box的真值
    # --------------------------------------------------------------
    # 根据anchor和gtbox计算得真值（anchor和gtbox之间的偏差）
    # 输入是所有的anchors，以及与之IOU最大的那个GT，返回是一个N×4的矩阵，每行表示一个anchor与对应的IOU最大的GT的y,h回归
    """返回值里面，只有正例的回归是有效值"""
    bbox_targets = _compute_targets(anchors, gt_boxes[argmax_overlaps, :])

    # 一开始是将超出图像范围的anchor直接丢掉的，现在在加回来， 加回来的的标签全部置为-1
    # labels是内部anchor的分类， total_anchors是总的anchor数目， inds_inside是内部anchor的索引
    labels = _unmap(labels, total_anchors, inds_inside, fill=-1)  # 这些anchor的label是-1，也即dontcare
    bbox_targets = _unmap(bbox_targets, total_anchors, inds_inside, fill=0)  # 这些anchor的真值是0，也即没有值

    labels = labels.reshape((1, height, width, A))  # reshape一下label
    rpn_labels = labels

    # bbox_targets
    bbox_targets = bbox_targets.reshape((1, height, width, A * cfg.TRAIN.COORDINAE_NUM))  # reshape

    rpn_bbox_targets = bbox_targets

    return rpn_labels, rpn_bbox_targets


# data是内部anchor的分类， count是总的anchor数目， inds是内部anchor的索引
def _unmap(data, count, inds, fill=0):
    """ Unmap a subset of item (data) back to the original set of items (of
    size count) """
    if len(data.shape) == 1:
        ret = np.empty((count, ), dtype=np.float32)
        ret.fill(fill)
        ret[inds] = data
    else:
        ret = np.empty((count, ) + data.shape[1:], dtype=np.float32)
        ret.fill(fill)
        ret[inds, :] = data
    return ret


def bbox_transform(ex_rois, gt_rois):
    """
    computes the distance from ground-truth boxes to the given boxes, normed by their size
    :param ex_rois: n * 4 numpy array, given boxes, anchors boxes
    :param gt_rois: n * 4 numpy array, ground-truth boxes
    :return: deltas: n * 4 numpy array, ground-truth boxes
    """
    ex_widths = ex_rois[:, 2] - ex_rois[:, 0] + 1
    for mywidth in ex_widths:
        assert mywidth == 16

    length = ex_rois.shape[0]
    # 取出所有正例
    inds_positive = np.where(ex_rois == 1)[0]

    # 计算正例的高度
    ex_heights = np.empty(shape=(length,), dtype=np.float32)
    ex_heights[inds_positive] = ex_rois[inds_positive, 3] - ex_rois[inds_positive, 1] + 1.0

    # 计算正例的中心坐标
    ex_ctr_y = np.empty(shape=(length,), dtype=np.float32)
    ex_ctr_y[inds_positive] = ex_rois[inds_positive, 1] + 0.5 * ex_heights

    assert np.min(ex_widths) > 0.1 and np.min(ex_heights) > 0.1, \
        'Invalid boxes found: {} {}'. \
            format(ex_rois[np.argmin(ex_widths), :], ex_rois[np.argmin(ex_heights), :])

    gt_heights = np.empty(shape=(length,), dtype=np.float32)
    gt_heights[inds_positive] = gt_rois[inds_positive, 3] - gt_rois[inds_positive, 1] + 1.0

    gt_ctr_y = np.empty(shape=(length,), dtype=np.float32)
    gt_ctr_y[inds_positive] = gt_rois[inds_positive, 1] + 0.5 * gt_heights[inds_positive]

    """
    对于ctopn文本检测，只需要回归y和高度坐标即可
    """
    targets_dy = np.empty(shape=(length,), dtype=np.float32)
    targets_dy[inds_positive] = (gt_ctr_y[inds_positive] - ex_ctr_y[inds_positive]) / ex_heights[inds_positive]

    targets_dh = np.empty(shape=(length,), dtype=np.float32)
    targets_dh[inds_positive] = np.log(gt_heights[inds_positive] / ex_heights[inds_positive])

    targets = np.vstack(
        (targets_dy, targets_dh)).transpose()

    return targets


def _compute_targets(ex_rois, gt_rois):
    """Compute bounding-box regression targets for an image."""

    assert ex_rois.shape[0] == gt_rois.shape[0]
    assert ex_rois.shape[1] == 4
    assert gt_rois.shape[1] == 4
    """
    到这里为止， 用下面的代码验证了 ex_rois的宽度全部为16
    mywidth = ex_rois[:, 2]-ex_rois[:, 0] + 1
    for i in mywidth:
        if i != 16:
            print("=============++++++++++++++===========", mywidth)
    """

    # bbox_transform函数的输入是anchors， 和GT的坐标部分
    # 输出是一个N×4的矩阵，每行表示一个anchor与对应的IOU最大的GT的y,h回归,
    return bbox_transform(ex_rois, gt_rois).astype(np.float32, copy=False)


