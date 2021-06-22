import numpy as np
import tensorflow as tf
from tensorflow.keras.models import Model
from tensorflow.keras import layers
import tensorflow.keras.backend as K
from utils import get_env_variable
import matplotlib.pyplot as plt
import os
import cv2
from itertools import product


def tversky_loss(y_true, y_pred):
    alpha = 0.5
    beta = 0.5
    ones = K.ones(K.shape(y_true))
    p0 = y_pred  # proba that voxels are class i
    p1 = ones - y_pred  # proba that voxels are not class i
    g0 = y_true
    g1 = ones - y_true
    num = K.sum(p0 * g0, (0, 1, 2, 3))
    den = num + alpha * K.sum(p0 * g1, (0, 1, 2, 3)) + beta * K.sum(p1 * g0, (0, 1, 2, 3))
    T = K.sum(num / den)  # when summing over classes, T has dynamic range [0 Ncl]
    Ncl = K.cast(K.shape(y_true)[-1], 'float32')

    return Ncl - T





def get_model(img_size, num_classes):
    # https://keras.io/examples/vision/oxford_pets_image_segmentation/#what-does-one-input-image-and-corresponding-segmentation-mask-look-like

    inputs = tf.keras.Input(shape=img_size + (3,))

    ### [First half of the network: downsampling inputs] ###

    # Entry block
    x = layers.Conv2D(32, 3, strides=2, padding="same")(inputs)
    x = layers.BatchNormalization()(x)
    x = layers.Activation("relu")(x)

    previous_block_activation = x  # Set aside residual

    # Blocks 1, 2, 3 are identical apart from the feature depth.
    for filters in [128, 256, 528]:
        x = layers.Activation("relu")(x)
        x = layers.SeparableConv2D(filters, 3, padding="same")(x)
        x = layers.BatchNormalization()(x)

        x = layers.Activation("relu")(x)
        x = layers.SeparableConv2D(filters, 3, padding="same")(x)
        x = layers.BatchNormalization()(x)

        x = layers.MaxPooling2D(3, strides=2, padding="same")(x)

        # Project residual
        residual = layers.Conv2D(filters, 1, strides=2, padding="same")(
            previous_block_activation
        )
        x = layers.add([x, residual])  # Add back residual
        previous_block_activation = x  # Set aside next residual

    ### [Second half of the network: upsampling inputs] ###

    for filters in [528, 256, 128, 32]:
        x = layers.Activation("relu")(x)
        x = layers.Conv2DTranspose(filters, 3, padding="same")(x)
        x = layers.BatchNormalization()(x)

        x = layers.Activation("relu")(x)
        x = layers.Conv2DTranspose(filters, 3, padding="same")(x)
        x = layers.BatchNormalization()(x)

        x = layers.UpSampling2D(2)(x)

        # Project residual
        residual = layers.UpSampling2D(2)(previous_block_activation)
        residual = layers.Conv2D(filters, 1, padding="same")(residual)
        x = layers.add([x, residual])  # Add back residual
        previous_block_activation = x  # Set aside next residual

    # Add a per-pixel classification layer
    outputs = layers.Conv2D(num_classes, (1, 1), activation='softmax', padding='same')(x)

    # Define the model
    model = tf.keras.Model(inputs, outputs)
    return model


def mean_IoU(y_true, y_pred):
    '''
    https://www.tensorflow.org/api_docs/python/tf/keras/metrics/MeanIoU
    Implementation of the MeanIou
    '''
    number_classes = 3
    # print(y_true.shape) #(8, 256, 256, 3)
    # print(y_pred.shape) #(8, 256, 256, 3)

    eps = 1e-6
    number_items_in_batches = y_true.shape[0]
    IoU_mean = 0
    for b in range(number_items_in_batches):
        IoU_channel = 0
        pred = np.argmax(y_pred[b, :, :], axis=-1)
        mask_pred = np.zeros((int(get_env_variable('HEIGHT')), int(get_env_variable('WIDTH')), 3), dtype=int)
        for c in range(number_classes):
            # create mask
            mask_pred[:, :, c] = pred == c
            mask_pred_one_channel = mask_pred[:, :, c]
            mask = y_true[b, :, :, c].numpy().astype(int)  # 256 x 256

            # IOU = true_positive / (true_positive + false_positive + false_negative).

            true_positive = (mask & mask_pred_one_channel).sum()

            false_negative = (mask - mask_pred_one_channel)
            false_negative[false_negative == -1] = 0
            false_negative = false_negative.sum()

            false_positive = (mask_pred_one_channel - mask)
            false_positive[false_positive == -1] = 0
            false_positive = false_positive.sum()

            IoU_channel += true_positive / (true_positive + false_positive + false_negative + eps)
        IoU_mean += IoU_channel / number_classes
    return IoU_mean / number_items_in_batches


def pixel_accuracy(y_true, y_pred):
    '''
    Implementation of the pixel accuracy metric
    '''
    number_items_in_batches = y_true.shape[0]
    sum_true = 0
    sum_total = 0

    for b in range(number_items_in_batches):
        pred_mask = np.argmax(y_pred[b], axis=-1)
        real_mask = np.argmax(y_true[b], axis=-1)

        sum_true += (pred_mask == real_mask).sum()
        sum_total += int(get_env_variable('HEIGHT')) * int(get_env_variable('WIDTH'))

    return sum_true / sum_total


def predict_mask_and_plot(img, mask, model, epoch=0, save=False):
    image = np.reshape(img, newshape=(1, img.shape[0], img.shape[1], img.shape[2]))  # / 255.
    WIDTH = int(get_env_variable('WIDTH'))
    HEIGHT = int(get_env_variable('HEIGHT'))
    pred = model.predict(image)

    res = np.argmax(pred[0], axis=-1)
    final = np.zeros((HEIGHT, WIDTH, 3))
    final[:, :, 0] = res == 0
    final[:, :, 1] = res == 1
    final[:, :, 2] = res == 2

    fig, axs = plt.subplots(1, 3)
    fig.set_size_inches(20, 6)
    axs[0].imshow(img), axs[0].set_title('Original Image')
    axs[1].imshow(mask * 255), axs[1].set_title('True Mask')
    axs[2].imshow(final), axs[2].set_title('Evaluation mode' if epoch == 0 else 'Pred mask epoch {}'.format(epoch))
    # delete background for overlay

    if save:
        plt.savefig(os.path.join(get_env_variable('TRAIN_DATA'), 'images', 'epoch{}.png'.format(epoch)))
    else:
        plt.show()


class Show_Intermediate_Pred(tf.keras.callbacks.Callback):

    def __init__(self, image, mask):
        self.image = image
        self.mask = mask

    def on_epoch_end(self, epoch, logs=None):
        predict_mask_and_plot(self.image, self.mask, self.model, epoch, save=True)


def get_lr_metric(optimizer):
    def lr(y_true, y_pred):
        return optimizer.lr

    return lr
