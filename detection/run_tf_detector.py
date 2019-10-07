######
#
# run_tf_detector.py
#
# Functions to load a TensorFlow detection model, run inference,
# render bounding boxes on images, and write out the resulting
# images (with bounding boxes).
#
# This script depends on nothing else in our repo, just standard pip
# installs.  It's a good way to test our detector on a handful of images and
# get super-satisfying, graphical results.  It's also a good way to see how
# fast a detector model will run on a particular machine.
#
# As a consequence of being completely self-contained, this script also 
# contains copies of a bunch of rendering functions whose current versions
# live in visualization_utils.py.
#
# This script is *not* a good way to process lots and lots of images; it loads all
# the images first, then runs the model.  If you want to run a detector (e.g. ours)
# on lots of images, you should check out:
#
# 1) run_tf_detector_batch.py (for local execution)
# 
# 2) https://github.com/microsoft/CameraTraps/tree/master/api/batch_processing
#    (for running large jobs on Azure ML)
#
# See the "test driver" cell for example invocation.
#
# If no output directory is specified, writes detections for c:\foo\bar.jpg to
# c:\foo\bar_detections.jpg .
#
######

#%% Constants, imports, environment

import argparse
import glob
import os
import sys
import time

import PIL
from PIL import ImageFont, ImageDraw
import humanfriendly
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.image as mpimg
import matplotlib.patches as patches
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import tensorflow as tf
from tqdm import tqdm

DEFAULT_CONFIDENCE_THRESHOLD = 0.85

# Stick this into filenames before the extension for the rendered result
DETECTION_FILENAME_INSERT = '_detections'

BOX_COLORS = ['b','g','r']
DEFAULT_LINE_WIDTH = 10
SHOW_CONFIDENCE_VALUES = False

# Suppress excessive tensorflow output
tf.logging.set_verbosity(tf.logging.ERROR)
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'


#%% Class constants

# Copied from annotation_constants.py in the interest of keeping this script completely
# self-contained.

# Categories assigned to bounding boxes
bbox_categories = [
    {'id': 0, 'name': 'empty'},
    {'id': 1, 'name': 'animal'},
    {'id': 2, 'name': 'person'},
    {'id': 3, 'name': 'group'},  # group of animals
    {'id': 4, 'name': 'vehicle'}
]

bbox_category_str_id_to_name = {}

# As this table is used in this file, we will be mapping string-formatted integers to
# class names
for cat in bbox_categories:
    bbox_category_str_id_to_name[str([cat['id']])] = cat['name']
    
#%% Core detection functions

def load_model(checkpoint):
    """
    Load a detection model (i.e., create a graph) from a .pb file
    """

    detection_graph = tf.Graph()
    with detection_graph.as_default():
        od_graph_def = tf.GraphDef()
        with tf.gfile.GFile(checkpoint, 'rb') as fid:
            serialized_graph = fid.read()
            od_graph_def.ParseFromString(serialized_graph)
            tf.import_graph_def(od_graph_def, name='')
    
    return detection_graph


def generate_detections(detection_graph,images):
    """
    boxes,scores,classes,images = generate_detections(detection_graph,images)

    Run an already-loaded detector network on a set of images.

    [images] can be a list of numpy arrays or a list of filenames.  Non-list inputs will be
    wrapped into a list.

    Boxes are returned in relative coordinates as (top, left, bottom, right); 
    x,y origin is the upper-left.
    
    [boxes] will be returned as a numpy array of size nImages x nDetections x 4.
    
    [scores] and [classes] will each be returned as a numpy array of size nImages x nDetections.
    
    [images] is a set of numpy arrays corresponding to the input parameter [images], which may have
    have been either arrays or filenames.    
    """

    if not isinstance(images,list):
        images = [images]
    else:
        images = images.copy()

    print('Loading images...')
    startTime = time.time()
    
    # Load images if they're not already numpy arrays
    # iImage = 0; image = images[iImage]
    for iImage,image in enumerate(tqdm(images)):
        if isinstance(image,str):
            
            # Load the image as an nparray of size h,w,nChannels
            
            # There was a time when I was loading with PIL and switched to mpimg,
            # but I can't remember why, and converting to RGB is a very good reason
            # to load with PIL, since mpimg doesn't give any indication of color 
            # order, which basically breaks all .png files.
            #
            # So if you find a bug related to using PIL, update this comment
            # to indicate what it was, but also disable .png support.
            image = PIL.Image.open(image).convert("RGB"); image = np.array(image)
            # image = mpimg.imread(image)
            
            # This shouldn't be necessary when loading with PIL and converting to RGB
            nChannels = image.shape[2]
            if nChannels > 3:
                print('Warning: trimming channels from image')
                image = image[:,:,0:3]
            images[iImage] = image
        else:
            assert isinstance(image,np.ndarray)

    elapsed = time.time() - startTime
    print("Finished loading {} file(s) in {}".format(len(images),
          humanfriendly.format_timespan(elapsed)))    
    
    boxes = []
    scores = []
    classes = []
    
    nImages = len(images)

    print('Running detector...')    
    startTime = time.time()
    firstImageCompleteTime = None
    
    with detection_graph.as_default():
        
        with tf.Session(graph=detection_graph) as sess:
            
            for iImage,imageNP in tqdm(enumerate(images)): 
                
                imageNP_expanded = np.expand_dims(imageNP, axis=0)
                image_tensor = detection_graph.get_tensor_by_name('image_tensor:0')
                box = detection_graph.get_tensor_by_name('detection_boxes:0')
                score = detection_graph.get_tensor_by_name('detection_scores:0')
                clss = detection_graph.get_tensor_by_name('detection_classes:0')
                num_detections = detection_graph.get_tensor_by_name('num_detections:0')
                
                # Actual detection
                (box, score, clss, num_detections) = sess.run(
                        [box, score, clss, num_detections],
                        feed_dict={image_tensor: imageNP_expanded})

                boxes.append(box)
                scores.append(score)
                classes.append(clss)
            
                if iImage == 0:
                    firstImageCompleteTime = time.time()
                    
            # ...for each image                
    
        # ...with tf.Session

    # ...with detection_graph.as_default()
    
    elapsed = time.time() - startTime
    if nImages == 1:
        print("Finished running detector in {}".format(humanfriendly.format_timespan(elapsed)))
    else:
        firstImageElapsed = firstImageCompleteTime - startTime
        remainingImagesElapsed = elapsed - firstImageElapsed
        remainingImagesTimePerImage = remainingImagesElapsed/(nImages-1)
        
        print("Finished running detector on {} images in {} ({} for the first image, {} for each subsequent image)".format(len(images),
              humanfriendly.format_timespan(elapsed),
              humanfriendly.format_timespan(firstImageElapsed),
              humanfriendly.format_timespan(remainingImagesTimePerImage)))
    
    nBoxes = len(boxes)
    
    # Currently "boxes" is a list of length nImages, where each element is shaped as
    #
    # 1,nDetections,4
    #
    # This implicitly banks on TF giving us back a fixed number of boxes, let's assert on this
    # to make sure this doesn't silently break in the future.
    nDetections = -1
    # iBox = 0; box = boxes[iBox]
    for iBox,box in enumerate(boxes):
        nDetectionsThisBox = box.shape[1]
        assert (nDetections == -1 or nDetectionsThisBox == nDetections), 'Detection count mismatch'
        nDetections = nDetectionsThisBox
        assert(box.shape[0] == 1)
    
    # "scores" is a length-nImages list of elements with size 1,nDetections
    assert(len(scores) == nImages)
    for(iScore,score) in enumerate(scores):
        assert score.shape[0] == 1
        assert score.shape[1] == nDetections
        
    # "classes" is a length-nImages list of elements with size 1,nDetections
    #
    # Still as floats, but really representing ints
    assert(len(classes) == nBoxes)
    for(iClass,c) in enumerate(classes):
        assert c.shape[0] == 1
        assert c.shape[1] == nDetections
            
    # Squeeze out the empty axis
    boxes = np.squeeze(np.array(boxes),axis=1)
    scores = np.squeeze(np.array(scores),axis=1)
    classes = np.squeeze(np.array(classes),axis=1).astype(int)
    
    # boxes is nImages x nDetections x 4
    assert(len(boxes.shape) == 3)
    assert(boxes.shape[0] == nImages)
    assert(boxes.shape[1] == nDetections)
    assert(boxes.shape[2] == 4)
    
    # scores and classes are both nImages x nDetections
    assert(len(scores.shape) == 2)
    assert(scores.shape[0] == nImages)
    assert(scores.shape[1] == nDetections)
    
    assert(len(classes.shape) == 2)
    assert(classes.shape[0] == nImages)
    assert(classes.shape[1] == nDetections)
    
    return boxes,scores,classes,images


#%% Rendering functions, copied from visualization_utils.py
    
def render_detection_bounding_boxes(detections, image,
                                    label_map={},
                                    classification_label_map={},
                                    confidence_threshold=0.8, thickness=4,
                                    classification_confidence_threshold=0.3,
                                    max_classifications=3):
    """
    Renders bounding boxes, label, and confidence on an image if confidence is above the threshold.
    
    This works with the output of the batch processing API.
    
    Supports classification, if the detection contains classification results according to the 
    API output version 1.0.

    Args:
        detections: detections on the image, example content:
            [
                {
                    "category": "2",
                    "conf": 0.996,
                    "bbox": [
                        0.0,
                        0.2762,
                        0.1234,
                        0.2458
                    ]
                }
            ]
            where the bbox coordinates are [x, y, width_box, height_box], (x, y) is upper left.
            Supports classification results, if *detections* have the format
            [
                {
                    "category": "2",
                    "conf": 0.996,
                    "bbox": [
                        0.0,
                        0.2762,
                        0.1234,
                        0.2458
                    ]
                    "classifications": [
                        ["3", 0.901],
                        ["1", 0.071],
                        ["4", 0.025]
                    ]
                }
            ]
        image: PIL.Image object, output of generate_detections.
        label_map: optional, mapping the numerical label to a string name. The type of the numerical label
            (default string) needs to be consistent with the keys in label_map; no casting is carried out.
        classification_label_map: optional, mapping of the string class labels to the actual class names.
            The type of the numerical label (default string) needs to be consistent with the keys in
            label_map; no casting is carried out.
        confidence_threshold: optional, threshold above which the bounding box is rendered.
        thickness: optional, rendering line thickness.

    image is modified in place.
    """

    display_boxes = []
    display_strs = []  # list of lists, one list of strings for each bounding box (to accommodate multiple labels)
    classes = []  # for color selection

    for detection in detections:

        score = detection['conf']
        if score > confidence_threshold:
            
            x1, y1, w_box, h_box = detection['bbox']
            display_boxes.append([y1, x1, y1 + h_box, x1 + w_box])
            clss = detection['category']
            label = label_map[clss] if clss in label_map else clss
            displayed_label = ['{}: {}%'.format(label, round(100 * score))]
            
            if 'classifications' in detection:
                
                # To avoid duplicate colors with detection-only visualization, offset
                # the classification class index by the number of detection classes
                clss = len(bbox_categories) + int(detection['classifications'][0][0])
                classifications = detection['classifications']
                if len(classifications) > max_classifications:
                    classifications = classifications[0:max_classifications]
                for classification in classifications:
                    p = classification[1]
                    if p < classification_confidence_threshold:
                        continue
                    class_key = classification[0]
                    if class_key in classification_label_map:
                        class_name = classification_label_map[class_key]
                    else:
                        class_name = class_key
                    displayed_label += ['{}: {:5.1%}'.format(class_name.lower(), classification[1])]
                    
            # ...if we have detection results
            display_strs.append(displayed_label)
            classes.append(clss)

        # ...if the confidence of this detection is above threshold
        
    # ...for each detection
    display_boxes = np.array(display_boxes)

    draw_bounding_boxes_on_image(image, display_boxes, classes,
                                 display_strs=display_strs, thickness=thickness)


# The following functions are modified versions of those at:
#
# https://github.com/tensorflow/models/blob/master/research/object_detection/utils/visualization_utils.py

COLORS = [
    'AliceBlue', 'Red', 'RoyalBlue', 'Gold', 'Chartreuse', 'Aqua',  'Azure', 'Beige', 'Bisque',
    'BlanchedAlmond', 'BlueViolet', 'BurlyWood', 'CadetBlue', 'AntiqueWhite',
    'Chocolate', 'Coral', 'CornflowerBlue', 'Cornsilk', 'Crimson', 'Cyan',
    'DarkCyan', 'DarkGoldenRod', 'DarkGrey', 'DarkKhaki', 'DarkOrange',
    'DarkOrchid', 'DarkSalmon', 'DarkSeaGreen', 'DarkTurquoise', 'DarkViolet',
    'DeepPink', 'DeepSkyBlue', 'DodgerBlue', 'FireBrick', 'FloralWhite',
    'ForestGreen', 'Fuchsia', 'Gainsboro', 'GhostWhite', 'GoldenRod',
    'Salmon', 'Tan', 'HoneyDew', 'HotPink', 'IndianRed', 'Ivory', 'Khaki',
    'Lavender', 'LavenderBlush', 'LawnGreen', 'LemonChiffon', 'LightBlue',
    'LightCoral', 'LightCyan', 'LightGoldenRodYellow', 'LightGray', 'LightGrey',
    'LightGreen', 'LightPink', 'LightSalmon', 'LightSeaGreen', 'LightSkyBlue',
    'LightSlateGray', 'LightSlateGrey', 'LightSteelBlue', 'LightYellow', 'Lime',
    'LimeGreen', 'Linen', 'Magenta', 'MediumAquaMarine', 'MediumOrchid',
    'MediumPurple', 'MediumSeaGreen', 'MediumSlateBlue', 'MediumSpringGreen',
    'MediumTurquoise', 'MediumVioletRed', 'MintCream', 'MistyRose', 'Moccasin',
    'NavajoWhite', 'OldLace', 'Olive', 'OliveDrab', 'Orange', 'OrangeRed',
    'Orchid', 'PaleGoldenRod', 'PaleGreen', 'PaleTurquoise', 'PaleVioletRed',
    'PapayaWhip', 'PeachPuff', 'Peru', 'Pink', 'Plum', 'PowderBlue', 'Purple',
    'RosyBrown', 'Aquamarine', 'SaddleBrown', 'Green', 'SandyBrown',
    'SeaGreen', 'SeaShell', 'Sienna', 'Silver', 'SkyBlue', 'SlateBlue',
    'SlateGray', 'SlateGrey', 'Snow', 'SpringGreen', 'SteelBlue', 'GreenYellow',
    'Teal', 'Thistle', 'Tomato', 'Turquoise', 'Violet', 'Wheat', 'White',
    'WhiteSmoke', 'Yellow', 'YellowGreen'
]


def draw_bounding_boxes_on_image(image,
                                 boxes,
                                 classes,
                                 thickness=4,
                                 display_strs=()):
    """
    Draws bounding boxes on image.

    Args:
      image: a PIL.Image object.
      boxes: a 2 dimensional numpy array of [N, 4]: (ymin, xmin, ymax, xmax).
             The coordinates are in normalized format between [0, 1].
      classes: a list of ints or strings (that can be cast to ints) corresponding to the class labels of the boxes.
             This is only used for selecting the color to render the bounding box in.
      thickness: line thickness. Default value is 4.
      display_strs: list of list of strings.
                             a list of strings for each bounding box.
                             The reason to pass a list of strings for a
                             bounding box is that it might contain
                             multiple labels.
    """

    boxes_shape = boxes.shape
    if not boxes_shape:
        return
    if len(boxes_shape) != 2 or boxes_shape[1] != 4:
        # print('Input must be of size [N, 4], but is ' + str(boxes_shape))
        return  # no object detection on this image, return
    for i in range(boxes_shape[0]):
        if display_strs:
            display_str_list = display_strs[i]
            draw_bounding_box_on_image(image,
                                       boxes[i, 0], boxes[i, 1], boxes[i, 2], boxes[i, 3],
                                       classes[i],
                                       thickness=thickness, display_str_list=display_str_list)


def draw_bounding_box_on_image(image,
                               ymin,
                               xmin,
                               ymax,
                               xmax,
                               clss=None,
                               thickness=4,
                               display_str_list=(),
                               use_normalized_coordinates=True,
                               label_font_size=16):
    """
    Adds a bounding box to an image.

    Bounding box coordinates can be specified in either absolute (pixel) or
    normalized coordinates by setting the use_normalized_coordinates argument.

    Each string in display_str_list is displayed on a separate line above the
    bounding box in black text on a rectangle filled with the input 'color'.
    If the top of the bounding box extends to the edge of the image, the strings
    are displayed below the bounding box.

    Args:
      image: a PIL.Image object.
      ymin: ymin of bounding box - upper left.
      xmin: xmin of bounding box.
      ymax: ymax of bounding box.
      xmax: xmax of bounding box.
      clss: str, the class of the object in this bounding box - will be cast to an int.
      thickness: line thickness. Default value is 4.
      display_str_list: list of strings to display in box
                        (each to be shown on its own line).
      use_normalized_coordinates: If True (default), treat coordinates
        ymin, xmin, ymax, xmax as relative to the image.  Otherwise treat
        coordinates as absolute.
    """
    if clss is None:
        color = COLORS[1]
    else:
        color = COLORS[int(clss) % len(COLORS)]

    draw = ImageDraw.Draw(image)
    im_width, im_height = image.size
    if use_normalized_coordinates:
        (left, right, top, bottom) = (xmin * im_width, xmax * im_width,
                                      ymin * im_height, ymax * im_height)
    else:
        (left, right, top, bottom) = (xmin, xmax, ymin, ymax)
    draw.line([(left, top), (left, bottom), (right, bottom),
               (right, top), (left, top)], width=thickness, fill=color)

    try:
        font = ImageFont.truetype('arial.ttf', label_font_size)
    except IOError:
        font = ImageFont.load_default()

    # If the total height of the display strings added to the top of the bounding
    # box exceeds the top of the image, stack the strings below the bounding box
    # instead of above.
    display_str_heights = [font.getsize(ds)[1] for ds in display_str_list]

    # Each display_str has a top and bottom margin of 0.05x.
    total_display_str_height = (1 + 2 * 0.05) * sum(display_str_heights)

    if top > total_display_str_height:
        text_bottom = top
    else:
        text_bottom = bottom + total_display_str_height

    # Reverse list and print from bottom to top.
    for display_str in display_str_list[::-1]:

        text_width, text_height = font.getsize(display_str)
        margin = np.ceil(0.05 * text_height)

        draw.rectangle(
            [(left, text_bottom - text_height - 2 * margin), (left + text_width,
                                                              text_bottom)],
            fill=color)

        draw.text(
            (left + margin, text_bottom - text_height - margin),
            display_str,
            fill='black',
            font=font)

        text_bottom -= (text_height + 2 * margin)



#%% Rendering functions

def render_bounding_box(box, score, classLabel, inputFileName, outputFileName=None,
                          confidenceThreshold=DEFAULT_CONFIDENCE_THRESHOLD,linewidth=DEFAULT_LINE_WIDTH):
    """
    Convenience wrapper to apply render_bounding_boxes to a single image
    """
    outputFileNames = []
    if outputFileName is not None:
        outputFileNames = [outputFileName]
    scores = [[score]]
    boxes = [[box]]
    render_bounding_boxes(boxes,scores,[classLabel],[inputFileName],outputFileNames,
                          confidenceThreshold,linewidth)


def render_bounding_boxes(boxes, scores, classes, inputFileNames, outputFileNames=[],
                          confidenceThreshold=DEFAULT_CONFIDENCE_THRESHOLD, 
                          linewidth=DEFAULT_LINE_WIDTH):
    """
    Render bounding boxes on the image files specified in [inputFileNames].  
    
    [boxes] and [scores] should be in the format returned by generate_detections, 
    specifically [top, left, bottom, right] in normalized units, where the
    origin is the upper-left.    
    
    "classes" is currently unused, it's a placeholder for adding text annotations
    later.
    """

    USE_VIS_UTILS_RENDERING = True
    
    nImages = len(inputFileNames)
    iImage = 0

    for iImage in range(0,nImages):

        inputFileName = inputFileNames[iImage]

        if iImage >= len(outputFileNames):
            outputFileName = ''
        else:
            outputFileName = outputFileNames[iImage]

        if len(outputFileName) == 0:
            name, ext = os.path.splitext(inputFileName)
            outputFileName = "{}{}{}".format(name,DETECTION_FILENAME_INSERT,ext)

        if USE_VIS_UTILS_RENDERING:
        
            """
            def render_detection_bounding_boxes(detections, image,
                                        label_map={},
                                        classification_label_map={},
                                        confidence_threshold=0.8, thickness=4,
                                        classification_confidence_threshold=0.3,
                                        max_classifications=3):
            """
            """
            detections: detections on the image, example content:
            [
                {
                    "category": "2",
                    "conf": 0.996,
                    "bbox": [
                        0.0,
                        0.2762,
                        0.1234,
                        0.2458
                    ]
                }
            ]

            ...where the bbox coordinates are [x, y, box_width, box_height].
            
            (0, 0) is the upper-left.  Coordinates are normalized.
            """
            image = PIL.Image.open(inputFileName).convert("RGB")
            detections = []
            for iBox in range(0,len(boxes)):
                bbox = [0,0,0.25,0.5] # boxes[iImage][iBox]
                detections.append({'category':str(classes[iImage][iBox]),
                          'conf':scores[iImage][iBox],
                          'bbox':bbox})
            #  ...for each box
            
            render_detection_bounding_boxes(detections, image,
                                        confidence_threshold=confidenceThreshold, 
                                        thickness=linewidth,
                                        label_map=bbox_category_str_id_to_name)
            image.save(outputFileName)
        
        else:
            
            image = mpimg.imread(inputFileName)
            iBox = 0; box = boxes[iImage][iBox]
            dpi = 100
            s = image.shape; imageHeight = s[0]; imageWidth = s[1]
            figsize = imageWidth / float(dpi), imageHeight / float(dpi)
    
            plt.figure(figsize=figsize)
            ax = plt.axes([0,0,1,1])
            
            # Display the image
            ax.imshow(image)
            ax.set_axis_off()
        
            # plt.show()
            for iBox,box in enumerate(boxes[iImage]):
    
                score = scores[iImage][iBox]
                if score < confidenceThreshold:
                    continue
    
                # top, left, bottom, right 
                #
                # x,y origin is the upper-left
                topRel = box[0]
                leftRel = box[1]
                bottomRel = box[2]
                rightRel = box[3]
                
                x = leftRel * imageWidth
                y = topRel * imageHeight
                w = (rightRel-leftRel) * imageWidth
                h = (bottomRel-topRel) * imageHeight
                
                # Location is the bottom-left of the rect
                #
                # Origin is the upper-left
                iLeft = x
                iBottom = y
                iClass = int(classes[iImage][iBox])
                
                boxColor = BOX_COLORS[iClass % len(BOX_COLORS)]
                rect = patches.Rectangle((iLeft,iBottom),w,h,linewidth=linewidth,edgecolor=boxColor,
                                         facecolor='none')
                
                # Add the patch to the Axes
                ax.add_patch(rect)        
                
                if SHOW_CONFIDENCE_VALUES:
                    pLabel = 'Class {} ({:.2f})'.format(iClass,score)
                    ax.text(iLeft+5,iBottom+5,pLabel,color=boxColor,fontsize=12,
                            verticalalignment='top',bbox=dict(facecolor='black'))
                
            # ...for each box
    
            # This is magic goop that removes whitespace around image plots (sort of)        
            plt.subplots_adjust(top = 1, bottom = 0, right = 1, left = 0, hspace = 0, 
                                wspace = 0)
            plt.margins(0,0)
            ax.xaxis.set_major_locator(ticker.NullLocator())
            ax.yaxis.set_major_locator(ticker.NullLocator())
            ax.axis('tight')
            ax.set(xlim=[0,imageWidth],ylim=[imageHeight,0],aspect=1)
            plt.axis('off')                
    
            # plt.savefig(outputFileName, bbox_inches='tight', pad_inches=0.0, dpi=dpi, transparent=True)
            plt.savefig(outputFileName, dpi=dpi, transparent=True, optimize=True, quality=90)
            plt.close()
            # os.startfile(outputFileName)

    # ...for each image

# ...def render_bounding_boxes


def load_and_run_detector(modelFile, imageFileNames, outputDir=None,
                          confidenceThreshold=DEFAULT_CONFIDENCE_THRESHOLD, detection_graph=None):
    
    if len(imageFileNames) == 0:        
        print('Warning: no files available')
        return
        
    # Load and run detector on target images
    print('Loading model...')
    startTime = time.time()
    if detection_graph is None:
        detection_graph = load_model(modelFile)
    elapsed = time.time() - startTime
    print("Loaded model in {}".format(humanfriendly.format_timespan(elapsed)))
    
    boxes,scores,classes,images = generate_detections(detection_graph,imageFileNames)
    
    assert len(boxes) == len(imageFileNames)
    
    print('Rendering output...')
    startTime = time.time()
    
    outputFullPaths = []
    outputFileNames = {}
    
    if outputDir is not None:
            
        os.makedirs(outputDir,exist_ok=True)
        
        for iFn,fullInputPath in enumerate(tqdm(imageFileNames)):
            
            fn = os.path.basename(fullInputPath).lower()            
            name, ext = os.path.splitext(fn)
            fn = "{}{}{}".format(name,DETECTION_FILENAME_INSERT,ext)
            
            # Since we'll be writing a bunch of files to the same folder, rename
            # as necessary to avoid collisions
            if fn in outputFileNames:
                nCollisions = outputFileNames[fn]
                fn = str(nCollisions) + '_' + fn
                outputFileNames[fn] = nCollisions + 1
            else:
                outputFileNames[fn] = 0
            outputFullPaths.append(os.path.join(outputDir,fn))
    
        # ...for each file
        
    # ...if we're writing files to a directory other than the input directory
    
    plt.ioff()
    
    render_bounding_boxes(boxes=boxes, scores=scores, classes=classes, 
                          inputFileNames=imageFileNames, outputFileNames=outputFullPaths,
                          confidenceThreshold=confidenceThreshold)
    
    elapsed = time.time() - startTime
    print("Rendered output in {}".format(humanfriendly.format_timespan(elapsed)))
    
    return detection_graph


#%% Interactive driver

if False:
    
    #%%
    
    detection_graph = None
    
    #%%
    
    modelFile = r'D:\temp\models\megadetector_v3.pb'
    imageDir = r'D:\temp\demo_images\ssmini'    
    imageFileNames = [fn for fn in glob.glob(os.path.join(imageDir,'*.jpg'))
         if (not 'detections' in fn)]
    # imageFileNames = [r"D:\temp\test\1\NE2881-9_RCNX0195_xparent.png"]
    
    detection_graph = load_and_run_detector(modelFile,imageFileNames,
                                            confidenceThreshold=DEFAULT_CONFIDENCE_THRESHOLD,
                                            detection_graph=detection_graph)
    

#%% File helper functions

imageExtensions = ['.jpg','.jpeg','.gif','.png']
    
def isImageFile(s):
    """
    Check a file's extension against a hard-coded set of image file extensions    '
    """
    ext = os.path.splitext(s)[1]
    return ext.lower() in imageExtensions
    
    
def findImageStrings(strings):
    """
    Given a list of strings that are potentially image file names, look for strings
    that actually look like image file names (based on extension).
    """
    imageStrings = []
    bIsImage = [False] * len(strings)
    for iString,f in enumerate(strings):
        bIsImage[iString] = isImageFile(f) 
        if bIsImage[iString]:
            imageStrings.append(f)
        
    return imageStrings

    
def findImages(dirName,bRecursive=False):
    """
    Find all files in a directory that look like image file names
    """
    if bRecursive:
        strings = glob.glob(os.path.join(dirName,'**','*.*'), recursive=True)
    else:
        strings = glob.glob(os.path.join(dirName,'*.*'))
        
    imageStrings = findImageStrings(strings)
    
    return imageStrings

    
#%% Command-line driver
    
def main():
    
    # python run_tf_detector.py "D:\temp\models\object_detection\megadetector\megadetector_v2.pb" --imageFile "D:\temp\demo_images\test\S1_J08_R1_PICT0120.JPG"
    # python run_tf_detector.py "D:\temp\models\object_detection\megadetector\megadetector_v2.pb" --imageDir "d:\temp\demo_images\test"
    # python run_tf_detector.py "d:\temp\models\megadetector_v3.pb" --imageDir "d:\temp\test\in" --outputDir "d:\temp\test\out"
    
    parser = argparse.ArgumentParser()
    parser.add_argument('detectorFile', type=str)
    parser.add_argument('--imageDir', action='store', type=str, default='', 
                        help='Directory to search for images, with optional recursion')
    parser.add_argument('--imageFile', action='store', type=str, default='', 
                        help='Single file to process, mutually exclusive with imageDir')
    parser.add_argument('--threshold', action='store', type=float, 
                        default=DEFAULT_CONFIDENCE_THRESHOLD, 
                        help="Confidence threshold, don't render boxes below this confidence")
    parser.add_argument('--recursive', action='store_true', 
                        help='Recurse into directories, only meaningful if using --imageDir')
    parser.add_argument('--forceCpu', action='store_true', 
                        help='Force CPU detection, even if a GPU is available')
    parser.add_argument('--outputDir', type=str, default=None, 
                       help='Directory for output images (defaults to same as input)')
    
    if len(sys.argv[1:])==0:
        parser.print_help()
        parser.exit()
    
    args = parser.parse_args()    
    
    if len(args.imageFile) > 0 and len(args.imageDir) > 0:
        raise Exception('Cannot specify both image file and image dir')
    elif len(args.imageFile) == 0 and len(args.imageDir) == 0:
        raise Exception('Must specify either an image file or an image directory')
        
    if len(args.imageFile) > 0:
        imageFileNames = [args.imageFile]
    else:
        imageFileNames = findImages(args.imageDir,args.recursive)

    if args.forceCpu:
        os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        os.environ['CUDA_VISIBLE_DEVICES'] = '-1'

    # Hack to avoid running on already-detected images
    imageFileNames = [x for x in imageFileNames if DETECTION_FILENAME_INSERT not in x]
                
    print('Running detector on {} images'.format(len(imageFileNames)))    
    
    load_and_run_detector(modelFile=args.detectorFile, imageFileNames=imageFileNames, 
                          confidenceThreshold=args.threshold, outputDir=args.outputDir)
    

if __name__ == '__main__':
    
    main()
