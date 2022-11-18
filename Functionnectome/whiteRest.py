#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WhiteRest module

Explore the white matter of RSNs from the WhiteRest Atlas, and determine
the potential impact of white matter lesions on RSNs.

The minimal inputs should be (in that order):
    - The ROI you wish to explore (as a nifti file in the MNI space)
    - The white matter maps of WhiteRest atlas* (input as one 4D nifti file)
    - The RSN labels* information from the atlas (input as a .txt file)

The program also uses the disconnectome** of the input lesion to estimate the impact of the lesion on each RSN.
By default, the disconnectome is computed using the quickDisco program (part of the Functionnectome toolbox),
using the same white matter priors as the one used to generate the WhiteRest atlas.
Atlernatively, it is possible to replace the input ROI with the corresponding disconnectome (if it was already
computed). In this case, the "--disco" option will need to be specified.

WhiteRest will output a table with the Presence score for each RSN in the ROI,
both in % and raw score, as well as a few other metrics.
If no output file is given (with the "-o" option), the table will be printed in
the terminal. Otherwise, it will be saved as a text file, which can be imported
to a spreadsheet software (such as Excel) for further processing.

The software also gives the possibility to save a pie-chart summary of the results.


*: The atlas and label files can be downloaded from:
    https://www.dropbox.com/s/mo4zs159rqhqopv/WhiteRest.zip?dl=0
or, if there is a problem with the link, uppon request to:
    victor.nozais@gmail.com
**: Disconnectome original paper: https://doi.org/10.1038/s41467-020-18920-9
"""

import nibabel as nib
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd
import argparse
import os

try:
    from Functionnectome.quickDisco import probaMap_fromROI, checkH5
except ModuleNotFoundError:
    print(
        "The Functionnectome module was not found (probably not installed via pip)."
        " Importing functions from the folder where the current script was saved..."
    )
    from functionnectome import probaMap_fromROI, checkH5

pd.set_option("display.max_rows", None, "display.max_columns", None)

# %%


def _build_arg_parser():
    p = argparse.ArgumentParser(formatter_class=argparse.RawTextHelpFormatter,
                                description=__doc__)
    # Positional argument (obligatory)
    p.add_argument('in_ROI',
                   help='Path of the ROI file (.nii, .nii.gz).')
    p.add_argument('atlas_maps',
                   help='Path of the RSN atlas (.nii, nii.gz).')
    p.add_argument('atlas_labels',
                   help='Path to the atlas labels identifying the RSNs.')

    # Optional arguments
    p.add_argument('-ot', '--out_table',
                   help='Path to save the presence results (.txt or .csv).')
    p.add_argument('-od', '--out_disco',
                   help="Path to save the lesion's disconnectome (.nii or .nii.gz), if computed.")
    p.add_argument('-z', '--Z_thresh',
                   default=7,
                   help='Threshold to apply to the atlas z-maps (default z>7).')
    p.add_argument('-b', '--binarize',
                   action='store_true',
                   help='Binarize the maps after thresholding.')
    p.add_argument('-p', '--out_pie',
                   help='Path to save a pie-chart figure of the results (.png).')
    p.add_argument('-pt', '--thr_low_pie',
                   default=5,
                   help='Presence %% under which the RSNs are grouped on the pie-chart (default <5%%).')
    p.add_argument('-d', '--disco',
                   action='store_true',
                   help='To be specified when the "in_ROI" input given is not the lesion but '
                        'the disconncetome of the lesion')
    p.add_argument('-m', '--multiproc',
                   default=1,
                   help='Number of processes to run in parallel (default = 1).')
    return p


def checkOutFile(parser, path):
    path = os.path.abspath(path)
    pathDir = os.path.dirname(path)
    nameFile = os.path.basename(path)
    if not os.path.isdir(pathDir):
        parser.error(
            f"The directory of the output file '{nameFile}' does not exist."
            f"Please change the output directory or create it ({pathDir})."
        )
    if os.path.isfile(path):
        parser.error(
            f"The output file '{path}' already exists. Change the name or delete it."
        )


def checkInFile(parser, path):
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        parser.error(
            f"The input file '{path}' does not exists. Check and correct the path, then retry."
        )


def computPresence(ROI_f, atlas_f, RSNlabels_f, zThresh=7, binarize=False, saveDisco=None, disco=False, proc=1):
    '''
    Compute the presence score

    Parameters
    ----------
    ROI_f : str
        Filepath of the input ROI file (3D nifti in MNI 2mm space).
    atlas_f : str
        Filepath of the RSN white matter atlas (4D nifti in MNI 2mm space).
    RSNlabels_f : str
        Filepath of the text file with the labels and names of each RSN.
    zThresh : float
        Threshold to apply to the atlas (default = 7)
    binarize : bool
        Wether to binerize the thresholded RSN.
    saveDisco : str
        (opt) Path to save the computed disconnectome
    disco : bool
        Set to True if ROI_f is the disconnectome of the lesion (instead of the lesion itself).
    proc : int
        Number of parallel processes used to compute the disconnectome

    Returns
    -------
    A Pandas Dataframe with the presence score of each involved RSN

    '''
    atlas_im = nib.load(atlas_f)
    atlas = atlas_im.get_fdata()
    RSNlabels = pd.read_csv(RSNlabels_f, sep='\t')

    if disco:
        discoI = nib.load(ROI_f)
        discoV = discoI.get_fdata(dtype='float32')
        disco_affine = discoI.affine.copy()
        if not (discoI.affine == atlas_im.affine).all():
            disco_affine[0] = -disco_affine[0]
            if (disco_affine == atlas_im.affine).all():
                discoV = np.flip(discoV)
            else:
                print("Orientation (affine) of the input disconnectome volume not recognized. "
                      "Expected orientation matrix:")
                print(atlas_im.affine)
                print('Affine of the input ROI:')
                print(discoI.affine)
                raise ValueError('Wrong data orientation, or not in 2x2x2mm MNI152 space.')
    else:
        # Check if the priors V1 (used for WhiteRest) are available, and retrieve them if so
        priorsOK, txtH5, h5Labels, priors_paths = checkH5()
        if 'V1.D.WB' not in h5Labels:
            txtError = (
                'The Functionnectome priors necessary for the disconnectome analysis are missing.\n'
                'Please download the "V1.D.WB" priors using the Functionnectome GUI.\n'
                '(to do so, launch the "FunctionnectomeGUI" command in a terminal, select the priors\n'
                'and click "Manual download".'
            )
            raise FileNotFoundError(txtError)
        priorsF = priors_paths['V1.D.WB - Whole brain, Deterministic (legacy)']
        discoI = probaMap_fromROI(ROI_f, priorsF, 'h5', proc=proc, maxVal=True)
        discoV = discoI.get_fdata(dtype='float32')
        if saveDisco:
            nib.save(discoI, saveDisco)

    if len(RSNlabels) != atlas.shape[3]:
        raise IndexError('Number of RSNs in the atlas and the label file not the same')

    if RSNlabels.iloc[-1]['RSN name'] == 'Cerebellum':  # Removing the cerebellum RSN from the analysis
        RSNlabels = RSNlabels.drop(RSNlabels.index[-1])
        atlas = atlas[:, :, :, :-1]
    atlas = np.nan_to_num(atlas)
    totalPresRSN = [atlas[..., i].sum() for i in range(atlas.shape[-1])]
    resPresence = pd.DataFrame(columns=('RSN number', 'RSN name', 'Presence (%)', 'Presence (raw)'))

    atlas[atlas < zThresh] = 0
    if binarize:
        atlas[atlas > 0] = 1

    # Presence computation, all RSNs at the same time
    atlasDisco = atlas * np.expand_dims(discoV, -1)
    totalPresRSN = atlas.sum((0, 1, 2))
    rawPres = atlasDisco.sum((0, 1, 2))  # sum along the 3 spatial dimensions
    presence = 100 * rawPres / totalPresRSN

    for i in range(atlas.shape[-1]):
        resPresence.loc[i] = [RSNlabels.loc[i, 'RSN number'],  # RSN number
                              RSNlabels.loc[i, 'RSN name'],  # RSN name
                              presence[i],  # Presence (%)
                              rawPres[i],  # Presence (raw)
                              ]
    resPresence.sort_values('Presence (%)', ascending=False, inplace=True, ignore_index=True)
    return resPresence


def make_fun_autopct(res):
    def fun_autopct(pct):
        res_array = res['Presence (%)'].values
        res_pct = 100 * res_array / res_array.sum()
        ind_pct = (np.abs(res_pct - pct)).argmin()  # Get the index closest to the input % (pct)
        pres = res_array[ind_pct]
        return "{:.1f}%\n({:.1f}%)".format(pres, pct)
    return fun_autopct


def make_fun_autopct_withThr(res):
    def fun_autopct(pct):
        res_array = res['Presence (%)'].values
        res_pct = 100 * res_array / res_array.sum()
        ind_pct = (np.abs(res_pct - pct)).argmin()
        if ind_pct == 0:
            return "({:.1f})%".format(pct)
        else:
            pres = res_array[ind_pct]
            return "{:.1f}%\n({:.1f}%)".format(pres, pct)
    return fun_autopct


def plot_pie(res, outFile, thresh_percent):
    lowrsn = (res['Presence (%)'] < thresh_percent)
    lowrsnNb = lowrsn.sum()
    res_thr = res.copy()
    sumLow = 0
    if lowrsnNb > 1:  # If there are RSNs with presence under the threshold, group them together
        for i in lowrsn[lowrsn].index:
            sumLow += res.loc[i, 'Presence (%)']
            res_thr = res_thr.drop(i)
        res_thr = pd.concat(
            [
                pd.DataFrame({'RSN number': [f'< {thresh_percent}% ({lowrsnNb} RSN)'],
                              'Presence (%)': [sumLow]}
                             ),
                res_thr
            ], ignore_index=True)
        cmap = plt.get_cmap('Spectral')
        colors = [cmap(i) for i in np.linspace(0, 1, len(res_thr))]
        expl = [0 for i in range(len(res_thr))]  # To put the low value appart in the pie-chart
        expl[0] = 0.1
        plt.figure(figsize=(10, 8), dpi=120)
        patches, texts, autotexts = plt.pie(res_thr['Presence (%)'],
                                            labels=res_thr['RSN number'],
                                            textprops={'fontsize': 12, 'font': 'Arial'},
                                            autopct=make_fun_autopct_withThr(res_thr),  # '%1.1f%%',
                                            shadow=False,
                                            colors=colors,
                                            explode=expl,
                                            pctdistance=0.8)
        for autotxt in autotexts:
            autotxt.set_fontsize(15)
        plt.savefig(outFile)
    else:
        cmap = plt.get_cmap('Spectral')
        colors = [cmap(i) for i in np.linspace(0, 1, len(res))]
        plt.figure(figsize=(10, 8), dpi=120)
        patches, texts, autotexts = plt.pie(res['Presence (%)'],
                                            labels=res['RSN number'],
                                            textprops={'fontsize': 12, 'font': 'Arial'},
                                            autopct=make_fun_autopct(res),  # '%1.1f%%',
                                            shadow=False,
                                            colors=colors,
                                            pctdistance=0.8)
        for autotxt in autotexts:
            autotxt.set_fontsize(15)
        plt.savefig(outFile)


def main():
    parser = _build_arg_parser()
    args = parser.parse_args()

    ROI_f = args.in_ROI
    atlas_f = args.atlas_maps
    RSNlabels_f = args.atlas_labels
    zThresh = float(args.Z_thresh)
    binarize = args.binarize
    svDisco = args.out_disco
    discoIn = args.disco
    proc = int(args.multiproc)

    checkInFile(parser, ROI_f)
    checkInFile(parser, atlas_f)
    checkInFile(parser, RSNlabels_f)

    if args.out_table:
        checkOutFile(parser, args.out_table)
    if args.out_pie:
        checkOutFile(parser, args.out_pie)

    res = computPresence(ROI_f, atlas_f, RSNlabels_f, zThresh, binarize, svDisco, discoIn, proc)

    if args.out_table:
        res.to_csv(args.out_table, sep='\t')
    else:
        print(res)

    if args.out_pie:
        res = res.sort_values('Presence (%)').reset_index()
        plot_pie(res, args.out_pie, float(args.thr_low_pie))


# %%
if __name__ == "__main__":
    main()
