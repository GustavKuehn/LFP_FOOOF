"""FOOOF Object - base object which defines the model.

Private Attributes
==================
Private attributes of the FOOOF object are documented here.

Data Attributes
---------------
_spectrum_flat : 1d array
    Flattened power spectrum, with the aperiodic component removed.
_spectrum_peak_rm : 1d array
    Power spectrum, with peaks removed.

Model Component Attributes
--------------------------
_ap_fit : 1d array
    Values of the isolated aperiodic fit.
_peak_fit : 1d array
    Values of the isolated peak fit.

Internal Settings Attributes
----------------------------
_ap_percentile_thresh : float
    Percentile threshold for finding peaks above the aperiodic component.
_ap_guess : list of [float, float, float]
    Guess parameters for fitting the aperiodic component.
_ap_bounds : tuple of tuple of float
    Upper and lower bounds on fitting aperiodic component.
_cf_bound : float
    Parameter bounds for center frequency when fitting gaussians.
_bw_std_edge : float
    Bandwidth threshold for edge rejection of peaks, in units of gaussian standard deviation.
_gauss_overlap_thresh : float
    Degree of overlap (in units of standard deviation) between gaussian guesses to drop one.
_gauss_std_limits : list of [float, float]
    Peak width limits, converted to use for gaussian standard deviation parameter.
    This attribute is computed based on `peak_width_limits` and should not be updated directly.
_maxfev : int
    The maximum number of calls to the curve fitting function.
_error_metric : str
    The error metric to use for post-hoc measures of model fit error.

Run Modes
---------
_debug : bool
    Whether the object is set in debug mode.
    This should be controlled by using the `set_debug_mode` method.
_check_data, _check_freqs : bool
    Whether to check added inputs for incorrect inputs, failing if present.
    Frequency data is checked for linear spacing.
    Power values are checked for data for NaN or Inf values.
    These modes default to True, and can be controlled with the `set_check_modes` method.

Code Notes
----------
Methods without defined docstrings import docs at runtime, from aliased external functions.
"""

import warnings
from copy import deepcopy

import numpy as np
from numpy.linalg import LinAlgError
from scipy.optimize import curve_fit

from fooof.core.utils import unlog
from fooof.core.items import OBJ_DESC
from fooof.core.info import get_indices
from fooof.core.io import save_fm, load_json
from fooof.core.reports import save_report_fm
from fooof.core.modutils import copy_doc_func_to_method
from fooof.core.utils import group_three, check_array_dim
from fooof.core.funcs import gaussian_function, get_ap_func, infer_ap_func
from fooof.core.jacobians import jacobian_gauss
from fooof.core.errors import (FitError, NoModelError, DataError,
                               NoDataError, InconsistentDataError)
from fooof.core.strings import (gen_settings_str, gen_results_fm_str,
                                gen_issue_str, gen_width_warning_str)

from fooof.plts.fm import plot_fm
from fooof.utils.data import trim_spectrum
from fooof.utils.params import compute_gauss_std
from fooof.data import FOOOFSettings, FOOOFRunModes, FOOOFMetaData, FOOOFResults
from fooof.data.conversions import model_to_dataframe
from fooof.sim.gen import gen_freqs, gen_aperiodic, gen_periodic, gen_model

###################################################################################################
###################################################################################################

class FOOOF():
    """Model a physiological power spectrum as a combination of aperiodic and periodic components.

    WARNING: FOOOF expects frequency and power values in linear space.

    Passing in logged frequencies and/or power spectra is not detected,
    and will silently produce incorrect results.

    Parameters
    ----------
    peak_width_limits : tuple of (float, float), optional, default: (0.5, 12.0)
        Limits on possible peak width, in Hz, as (lower_bound, upper_bound).
    max_n_peaks : int, optional, default: inf
        Maximum number of peaks to fit.
    min_peak_height : float, optional, default: 0
        Absolute threshold for detecting peaks.
        This threshold is defined in absolute units of the power spectrum (log power).
    peak_threshold : float, optional, default: 2.0
        Relative threshold for detecting peaks.
        This threshold is defined in relative units of the power spectrum (standard deviation).
    aperiodic_mode : {'fixed', 'knee'}
        Which approach to take for fitting the aperiodic component.
    verbose : bool, optional, default: True
        Verbosity mode. If True, prints out warnings and general status updates.

    Attributes
    ----------
    freqs : 1d array
        Frequency values for the power spectrum.
    power_spectrum : 1d array
        Power values, stored internally in log10 scale.
    freq_range : list of [float, float]
        Frequency range of the power spectrum, as [lowest_freq, highest_freq].
    freq_res : float
        Frequency resolution of the power spectrum.
    fooofed_spectrum_ : 1d array
        The full model fit of the power spectrum, in log10 scale.
    aperiodic_params_ : 1d array
        Parameters that define the aperiodic fit. As [Offset, (Knee), Exponent].
        The knee parameter is only included if aperiodic component is fit with a knee.
    peak_params_ : 2d array
        Fitted parameter values for the peaks. Each row is a peak, as [CF, PW, BW].
    gaussian_params_ : 2d array
        Parameters that define the gaussian fit(s).
        Each row is a gaussian, as [mean, height, standard deviation].
    r_squared_ : float
        R-squared of the fit between the input power spectrum and the full model fit.
    error_ : float
        Error of the full model fit.
    n_peaks_ : int
        The number of peaks fit in the model.
    has_data : bool
        Whether data is loaded to the object.
    has_model : bool
        Whether model results are available in the object.

    Notes
    -----
    - Commonly used abbreviations used in this module include:
      CF: center frequency, PW: power, BW: Bandwidth, AP: aperiodic
    - Input power spectra must be provided in linear scale.
      Internally they are stored in log10 scale, as this is what the model operates upon.
    - Input power spectra should be smooth, as overly noisy power spectra may lead to bad fits.
      For example, raw FFT inputs are not appropriate. Where possible and appropriate, use
      longer time segments for power spectrum calculation to get smoother power spectra,
      as this will give better model fits.
    - The gaussian params are those that define the gaussian of the fit, where as the peak
      params are a modified version, in which the CF of the peak is the mean of the gaussian,
      the PW of the peak is the height of the gaussian over and above the aperiodic component,
      and the BW of the peak, is 2*std of the gaussian (as 'two sided' bandwidth).
    """
    # pylint: disable=attribute-defined-outside-init

    def __init__(self, peak_width_limits=(0.5, 12.0), max_n_peaks=np.inf, min_peak_height=0.0,
                 peak_threshold=2.0, aperiodic_mode='fixed', verbose=True):
        """Initialize object with desired settings."""

        # Set input settings
        self.peak_width_limits = peak_width_limits
        self.max_n_peaks = max_n_peaks
        self.min_peak_height = min_peak_height
        self.peak_threshold = peak_threshold
        self.aperiodic_mode = aperiodic_mode
        self.verbose = verbose

        ## PRIVATE SETTINGS
        # Percentile threshold, to select points from a flat spectrum for an initial aperiodic fit
        #   Points are selected at a low percentile value to restrict to non-peak points
        self._ap_percentile_thresh = 0.025
        # Guess parameters for aperiodic fitting, [offset, knee, exponent]
        #   If offset guess is None, the first value of the power spectrum is used as offset guess
        #   If exponent guess is None, the abs(log-log slope) of first & last points is used
        self._ap_guess = (None, 0, None)
        # Bounds for aperiodic fitting, as: ((offset_low_bound, knee_low_bound, exp_low_bound),
        #                                    (offset_high_bound, knee_high_bound, exp_high_bound))
        # By default, aperiodic fitting is unbound, but can be restricted here, if desired
        #   Even if fitting without knee, leave bounds for knee (they are dropped later)
        self._ap_bounds = ((-np.inf, 0,0, 0), (np.inf, np.inf,np.inf, np.inf))
        # Threshold for how far a peak has to be from edge to keep.
        #   This is defined in units of gaussian standard deviation
        self._bw_std_edge = 1.0
        # Degree of overlap between gaussians for one to be dropped
        #   This is defined in units of gaussian standard deviation
        self._gauss_overlap_thresh = 0.75
        # Parameter bounds for center frequency when fitting gaussians, in terms of +/- std dev
        self._cf_bound = 1.5
        # The error metric to calculate, post model fitting. See `_calc_error` for options
        #   Note: this is for checking error post fitting, not an objective function for fitting
        self._error_metric = 'MAE'

        ## PRIVATE CURVE_FIT SETTINGS
        # The maximum number of calls to the curve fitting function
        self._maxfev = 500
        # The tolerance setting for curve fitting (see scipy.curve_fit - ftol / xtol / gtol)
        #   Here reduce tolerance to speed fitting. Set value to 1e-8 to match curve_fit default
        self._tol = 0.00001

        ## RUN MODES
        # Set default debug mode - controls if an error is raised if model fitting is unsuccessful
        self._debug = False
        # Set default data checking modes - controls which checks get run on input data
        #   check_freqs: checks the frequency values, and raises an error for uneven spacing
        self._check_freqs = True
        #   check_data: checks the power values and raises an error for any NaN / Inf values
        self._check_data = True

        # Set internal settings, based on inputs, and initialize data & results attributes
        self._reset_internal_settings()
        self._reset_data_results(True, True, True)


    @property
    def has_data(self):
        """Indicator for if the object contains data."""

        return True if np.any(self.power_spectrum) else False


    @property
    def has_model(self):
        """Indicator for if the object contains a model fit.

        Notes
        -----
        This check uses the aperiodic params, which are:

        - nan if no model has been fit
        - necessarily defined, as floats, if model has been fit
        """

        return True if not np.all(np.isnan(self.aperiodic_params_)) else False


    @property
    def n_peaks_(self):
        """How many peaks were fit in the model."""

        return self.peak_params_.shape[0] if self.has_model else None


    def _reset_internal_settings(self):
        """Set, or reset, internal settings, based on what is provided in init.

        Notes
        -----
        These settings are for internal use, based on what is provided to, or set in `__init__`.
        They should not be altered by the user.
        """

        # Only update these settings if other relevant settings are available
        if self.peak_width_limits:

            # Bandwidth limits are given in 2-sided peak bandwidth
            #   Convert to gaussian std parameter limits
            self._gauss_std_limits = tuple(bwl / 2 for bwl in self.peak_width_limits)

        # Otherwise, assume settings are unknown (have been cleared) and set to None
        else:
            self._gauss_std_limits = None


    def _reset_data_results(self, clear_freqs=False, clear_spectrum=False, clear_results=False):
        """Set, or reset, data & results attributes to empty.

        Parameters
        ----------
        clear_freqs : bool, optional, default: False
            Whether to clear frequency attributes.
        clear_spectrum : bool, optional, default: False
            Whether to clear power spectrum attribute.
        clear_results : bool, optional, default: False
            Whether to clear model results attributes.
        """

        if clear_freqs:
            self.freqs = None
            self.freq_range = None
            self.freq_res = None

        if clear_spectrum:
            self.power_spectrum = None

        if clear_results:

            self.aperiodic_params_ = np.array([np.nan] * \
                (2 if self.aperiodic_mode == 'fixed' else 3))
            self.gaussian_params_ = np.empty([0, 3])
            self.peak_params_ = np.empty([0, 3])
            self.r_squared_ = np.nan
            self.error_ = np.nan

            self.fooofed_spectrum_ = None

            self._spectrum_flat = None
            self._spectrum_peak_rm = None
            self._ap_fit = None
            self._peak_fit = None


    def add_data(self, freqs, power_spectrum, freq_range=None, clear_results=True):
        """Add data (frequencies, and power spectrum values) to the current object.

        Parameters
        ----------
        freqs : 1d array
            Frequency values for the power spectrum, in linear space.
        power_spectrum : 1d array
            Power spectrum values, which must be input in linear space.
        freq_range : list of [float, float], optional
            Frequency range to restrict power spectrum to.
            If not provided, keeps the entire range.
        clear_results : bool, optional, default: True
            Whether to clear prior results, if any are present in the object.
            This should only be set to False if data for the current results are being re-added.

        Notes
        -----
        If called on an object with existing data and/or results
        they will be cleared by this method call.
        """

        # If any data is already present, then clear previous data
        # Also clear results, if present, unless indicated not to
        #   This is to ensure object consistency of all data & results
        self._reset_data_results(clear_freqs=self.has_data,
                                 clear_spectrum=self.has_data,
                                 clear_results=self.has_model and clear_results)

        self.freqs, self.power_spectrum, self.freq_range, self.freq_res = \
            self._prepare_data(freqs, power_spectrum, freq_range, 1)


    def add_settings(self, fooof_settings):
        """Add settings into object from a FOOOFSettings object.

        Parameters
        ----------
        fooof_settings : FOOOFSettings
            A data object containing the settings for a FOOOF model.
        """

        for setting in OBJ_DESC['settings']:
            setattr(self, setting, getattr(fooof_settings, setting))

        self._check_loaded_settings(fooof_settings._asdict())


    def add_meta_data(self, fooof_meta_data):
        """Add data information into object from a FOOOFMetaData object.

        Parameters
        ----------
        fooof_meta_data : FOOOFMetaData
            A meta data object containing meta data information.
        """

        for meta_dat in OBJ_DESC['meta_data']:
            setattr(self, meta_dat, getattr(fooof_meta_data, meta_dat))

        self._regenerate_freqs()


    def add_results(self, fooof_result):
        """Add results data into object from a FOOOFResults object.

        Parameters
        ----------
        fooof_result : FOOOFResults
            A data object containing the results from fitting a FOOOF model.
        """

        self.aperiodic_params_ = fooof_result.aperiodic_params
        self.gaussian_params_ = fooof_result.gaussian_params
        self.peak_params_ = fooof_result.peak_params
        self.r_squared_ = fooof_result.r_squared
        self.error_ = fooof_result.error

        self._check_loaded_results(fooof_result._asdict())


    def report(self, freqs=None, power_spectrum=None, freq_range=None,
               plt_log=False, plot_full_range=False, **plot_kwargs):
        """Run model fit, and display a report, which includes a plot, and printed results.

        Parameters
        ----------
        freqs : 1d array, optional
            Frequency values for the power spectrum.
        power_spectrum : 1d array, optional
            Power values, which must be input in linear space.
        freq_range : list of [float, float], optional
            Desired frequency range to fit the model to.
            If not provided, fits across the entire given range.
        plt_log : bool, optional, default: False
            Whether or not to plot the frequency axis in log space.
        plot_full_range : bool, default: False
            If True, plots the full range of the given power spectrum.
            Only relevant / effective if `freqs` and `power_spectrum` passed in in this call.
        **plot_kwargs
            Keyword arguments to pass into the plot method.
            Plot options with a name conflict be passed by pre-pending `plot_`.
            e.g. `freqs`, `power_spectrum` and `freq_range`.

        Notes
        -----
        Data is optional, if data has already been added to the object.
        """

        self.fit(freqs, power_spectrum, freq_range)
        self.plot(plt_log=plt_log,
                  freqs=freqs if plot_full_range else plot_kwargs.pop('plot_freqs', None),
                  power_spectrum=power_spectrum if \
                      plot_full_range else plot_kwargs.pop('plot_power_spectrum', None),
                  freq_range=plot_kwargs.pop('plot_freq_range', None),
                  **plot_kwargs)
        self.print_results(concise=False)


    def fit(self, freqs=None, power_spectrum=None, freq_range=None):
        """Fit the full power spectrum as a combination of periodic and aperiodic components.

        Parameters
        ----------
        freqs : 1d array, optional
            Frequency values for the power spectrum, in linear space.
        power_spectrum : 1d array, optional
            Power values, which must be input in linear space.
        freq_range : list of [float, float], optional
            Frequency range to restrict power spectrum to. If not provided, keeps the entire range.

        Raises
        ------
        NoDataError
            If no data is available to fit.
        FitError
            If model fitting fails to fit. Only raised in debug mode.

        Notes
        -----
        Data is optional, if data has already been added to the object.
        """

        # If freqs & power_spectrum provided together, add data to object.
        if freqs is not None and power_spectrum is not None:
            self.add_data(freqs, power_spectrum, freq_range)
        # If power spectrum provided alone, add to object, and use existing frequency data
        #   Note: be careful passing in power_spectrum data like this:
        #     It assumes the power_spectrum is already logged, with correct freq_range
        elif isinstance(power_spectrum, np.ndarray):
            self.power_spectrum = power_spectrum

        # Check that data is available
        if not self.has_data:
            raise NoDataError("No data available to fit, can not proceed.")

        # Check and warn about width limits (if in verbose mode)
        if self.verbose:
            self._check_width_limits()

        # In rare cases, the model fails to fit, and so uses try / except
        try:

            # If not set to fail on NaN or Inf data at add time, check data here
            #   This serves as a catch all for curve_fits which will fail given NaN or Inf
            #   Because FitError's are by default caught, this allows fitting to continue
            if not self._check_data:
                if np.any(np.isinf(self.power_spectrum)) or np.any(np.isnan(self.power_spectrum)):
                    raise FitError("Model fitting was skipped because there are NaN or Inf "
                                   "values in the data, which preclude model fitting.")

            # Fit the aperiodic component
            self.aperiodic_params_ = self._robust_ap_fit(self.freqs, self.power_spectrum)
            self._ap_fit = gen_aperiodic(self.freqs, self.aperiodic_params_)

            # Flatten the power spectrum using fit aperiodic fit
            self._spectrum_flat = self.power_spectrum - self._ap_fit

            # Find peaks, and fit them with gaussians
            self.gaussian_params_ = self._fit_peaks2(np.copy(self._spectrum_flat))

            # Calculate the peak fit
            #   Note: if no peaks are found, this creates a flat (all zero) peak fit
            self._peak_fit = gen_periodic(self.freqs, np.ndarray.flatten(self.gaussian_params_))

            # Create peak-removed (but not flattened) power spectrum
            self._spectrum_peak_rm = self.power_spectrum - self._peak_fit

            # Run final aperiodic fit on peak-removed power spectrum
            #   This overwrites previous aperiodic fit, and recomputes the flattened spectrum
            self.aperiodic_params_ = self._simple_ap_fit(self.freqs, self._spectrum_peak_rm)
            print(self.aperiodic_params_ )
            self._ap_fit = gen_aperiodic(self.freqs, self.aperiodic_params_)
            self._spectrum_flat = self.power_spectrum - self._ap_fit
            
            add_iterations = 10
            if np.all(self.gaussian_params_ != [0, 0, 0]):
                for it in range(1, add_iterations+1):
    
                    self._spectrum_flat = self.power_spectrum - self._ap_fit
                    if it < add_iterations:
                        self.gaussian_params_ = self._fit_peaks(np.copy(self._spectrum_flat))
                    
                    else:
                        self.gaussian_params_ = np.zeros_like(self.gaussian_params_)
                        self.gaussian_params_ = self._fit_peaks(np.copy(self._spectrum_flat))
                        
                    self._peak_fit = gen_periodic(self.freqs, np.ndarray.flatten(self.gaussian_params_))
        
                    self._spectrum_peak_rm = self.power_spectrum - self._peak_fit
        
                    self.aperiodic_params_ = self._simple_ap_fit(self.freqs, self._spectrum_peak_rm)
                    self._ap_fit = gen_aperiodic(self.freqs, self.aperiodic_params_) # either remove this from the last iteration or, reiterate theta after sgamma again
            
            
            
#############
            

            # Create full power_spectrum model fit
            self.fooofed_spectrum_ = self._peak_fit + self._ap_fit

            # Convert gaussian definitions to peak parameters
            self.peak_params_ = self._create_peak_params(self.gaussian_params_)

            # Calculate R^2 and error of the model fit
            self._calc_r_squared()
            self._calc_error()

        except FitError:

            # If in debug mode, re-raise the error
            if self._debug:
                raise

            # Clear any interim model results that may have run
            #   Partial model results shouldn't be interpreted in light of overall failure
            self._reset_data_results(clear_results=True)

            # Print out status
            if self.verbose:
                print("Model fitting was unsuccessful.")


    def print_settings(self, description=False, concise=False):
        """Print out the current settings.

        Parameters
        ----------
        description : bool, optional, default: False
            Whether to print out a description with current settings.
        concise : bool, optional, default: False
            Whether to print the report in a concise mode, or not.
        """

        print(gen_settings_str(self, description, concise))


    def print_results(self, concise=False):
        """Print out model fitting results.

        Parameters
        ----------
        concise : bool, optional, default: False
            Whether to print the report in a concise mode, or not.
        """

        print(gen_results_fm_str(self, concise))


    @staticmethod
    def print_report_issue(concise=False):
        """Prints instructions on how to report bugs and/or problematic fits.

        Parameters
        ----------
        concise : bool, optional, default: False
            Whether to print the report in a concise mode, or not.
        """

        print(gen_issue_str(concise))


    def get_settings(self):
        """Return user defined settings of the current object.

        Returns
        -------
        FOOOFSettings
            Object containing the settings from the current object.
        """

        return FOOOFSettings(**{key : getattr(self, key) \
                             for key in OBJ_DESC['settings']})


    def get_run_modes(self):
        """Return run modes of the current object.

        Returns
        -------
        FOOOFRunModes
            Object containing the run modes from the current object.
        """

        return FOOOFRunModes(**{key.strip('_') : getattr(self, key) \
                             for key in OBJ_DESC['run_modes']})


    def get_meta_data(self):
        """Return data information from the current object.

        Returns
        -------
        FOOOFMetaData
            Object containing meta data from the current object.
        """

        return FOOOFMetaData(**{key : getattr(self, key) \
                             for key in OBJ_DESC['meta_data']})


    def get_data(self, component='full', space='log'):
        """Get a data component.

        Parameters
        ----------
        component : {'full', 'aperiodic', 'peak'}
            Which data component to return.
                'full' - full power spectrum
                'aperiodic' - isolated aperiodic data component
                'peak' - isolated peak data component
        space : {'log', 'linear'}
            Which space to return the data component in.
                'log' - returns in log10 space.
                'linear' - returns in linear space.

        Returns
        -------
        output : 1d array
            Specified data component, in specified spacing.

        Notes
        -----
        The 'space' parameter doesn't just define the spacing of the data component
        values, but rather defines the space of the additive data definition such that
        `power_spectrum = aperiodic_component + peak_component`.
        With space set as 'log', this combination holds in log space.
        With space set as 'linear', this combination holds in linear space.
        """

        if not self.has_data:
            raise NoDataError("No data available to fit, can not proceed.")
        assert space in ['linear', 'log'], "Input for 'space' invalid."

        if component == 'full':
            output = self.power_spectrum if space == 'log' else unlog(self.power_spectrum)
        elif component == 'aperiodic':
            output = self._spectrum_peak_rm if space == 'log' else \
                unlog(self.power_spectrum) / unlog(self._peak_fit)
        elif component == 'peak':
            output = self._spectrum_flat if space == 'log' else \
                unlog(self.power_spectrum) - unlog(self._ap_fit)
        else:
            raise ValueError('Input for component invalid.')

        return output


    def get_model(self, component='full', space='log'):
        """Get a model component.

        Parameters
        ----------
        component : {'full', 'aperiodic', 'peak'}
            Which model component to return.
                'full' - full model
                'aperiodic' - isolated aperiodic model component
                'peak' - isolated peak model component
        space : {'log', 'linear'}
            Which space to return the model component in.
                'log' - returns in log10 space.
                'linear' - returns in linear space.

        Returns
        -------
        output : 1d array
            Specified model component, in specified spacing.

        Notes
        -----
        The 'space' parameter doesn't just define the spacing of the model component
        values, but rather defines the space of the additive model such that
        `model = aperiodic_component + peak_component`.
        With space set as 'log', this combination holds in log space.
        With space set as 'linear', this combination holds in linear space.
        """

        if not self.has_model:
            raise NoModelError("No model fit results are available, can not proceed.")
        assert space in ['linear', 'log'], "Input for 'space' invalid."

        if component == 'full':
            output = self.fooofed_spectrum_ if space == 'log' else unlog(self.fooofed_spectrum_)
        elif component == 'aperiodic':
            output = self._ap_fit if space == 'log' else unlog(self._ap_fit)
        elif component == 'peak':
            output = self._peak_fit if space == 'log' else \
                unlog(self.fooofed_spectrum_) - unlog(self._ap_fit)
        else:
            raise ValueError('Input for component invalid.')

        return output


    def get_params(self, name, col=None):
        """Return model fit parameters for specified feature(s).

        Parameters
        ----------
        name : {'aperiodic_params', 'peak_params', 'gaussian_params', 'error', 'r_squared'}
            Name of the data field to extract.
        col : {'CF', 'PW', 'BW', 'offset', 'knee', 'exponent'} or int, optional
            Column name / index to extract from selected data, if requested.
            Only used for name of {'aperiodic_params', 'peak_params', 'gaussian_params'}.

        Returns
        -------
        out : float or 1d array
            Requested data.

        Raises
        ------
        NoModelError
            If there are no model fit parameters available to return.

        Notes
        -----
        If there are no fit peak (no peak parameters), this method will return NaN.
        """

        if not self.has_model:
            raise NoModelError("No model fit results are available to extract, can not proceed.")

        # If col specified as string, get mapping back to integer
        if isinstance(col, str):
            col = get_indices(self.aperiodic_mode)[col]

        # Allow for shortcut alias, without adding `_params`
        if name in ['aperiodic', 'peak', 'gaussian']:
            name = name + '_params'

        # Extract the request data field from object
        out = getattr(self, name + '_')

        # Periodic values can be empty arrays and if so, replace with NaN array
        if isinstance(out, np.ndarray) and out.size == 0:
            out = np.array([np.nan, np.nan, np.nan])

        # Select out a specific column, if requested
        if col is not None:

            # Extract column, & if result is a single value in an array, unpack from array
            out = out[col] if out.ndim == 1 else out[:, col]
            out = out[0] if isinstance(out, np.ndarray) and out.size == 1 else out

        return out


    def get_results(self):
        """Return model fit parameters and goodness of fit metrics.

        Returns
        -------
        FOOOFResults
            Object containing the model fit results from the current object.
        """

        return FOOOFResults(**{key.strip('_') : getattr(self, key) \
            for key in OBJ_DESC['results']})


    @copy_doc_func_to_method(plot_fm)
    def plot(self, plot_peaks=None, plot_aperiodic=True, freqs=None, power_spectrum=None,
             freq_range=None, plt_log=False, add_legend=True, save_fig=False, file_name=None,
             file_path=None, ax=None, data_kwargs=None, model_kwargs=None,
             aperiodic_kwargs=None, peak_kwargs=None, **plot_kwargs):

        plot_fm(self, plot_peaks=plot_peaks, plot_aperiodic=plot_aperiodic, freqs=freqs,
                power_spectrum=power_spectrum, freq_range=freq_range, plt_log=plt_log,
                add_legend=add_legend, save_fig=save_fig, file_name=file_name,
                file_path=file_path, ax=ax, data_kwargs=data_kwargs, model_kwargs=model_kwargs,
                aperiodic_kwargs=aperiodic_kwargs, peak_kwargs=peak_kwargs, **plot_kwargs)


    @copy_doc_func_to_method(save_report_fm)
    def save_report(self, file_name, file_path=None, plt_log=False,
                    add_settings=True, **plot_kwargs):

        save_report_fm(self, file_name, file_path, plt_log, add_settings, **plot_kwargs)


    @copy_doc_func_to_method(save_fm)
    def save(self, file_name, file_path=None, append=False,
             save_results=False, save_settings=False, save_data=False):

        save_fm(self, file_name, file_path, append, save_results, save_settings, save_data)


    def load(self, file_name, file_path=None, regenerate=True):
        """Load in a FOOOF formatted JSON file to the current object.

        Parameters
        ----------
        file_name : str or FileObject
            File to load data from.
        file_path : Path or str, optional
            Path to directory to load from. If None, loads from current directory.
        regenerate : bool, optional, default: True
            Whether to regenerate the model fit from the loaded data, if data is available.
        """

        # Reset data in object, so old data can't interfere
        self._reset_data_results(True, True, True)

        # Load JSON file, add to self and check loaded data
        data = load_json(file_name, file_path)
        self._add_from_dict(data)
        self._check_loaded_settings(data)
        self._check_loaded_results(data)

        # Regenerate model components, based on what is available
        if regenerate:
            if self.freq_res:
                self._regenerate_freqs()
            if np.all(self.freqs) and np.all(self.aperiodic_params_):
                self._regenerate_model()


    def copy(self):
        """Return a copy of the current object."""

        return deepcopy(self)


    def set_debug_mode(self, debug):
        """Set debug mode, which controls if an error is raised if model fitting is unsuccessful.

        Parameters
        ----------
        debug : bool
            Whether to run in debug mode.
        """

        self._debug = debug


    def set_check_modes(self, check_freqs=None, check_data=None):
        """Set check modes, which controls if an error is raised based on check on the inputs.

        Parameters
        ----------
        check_freqs : bool, optional
            Whether to run in check freqs mode, which checks the frequency data.
        check_data : bool, optional
            Whether to run in check data mode, which checks the power spectrum values data.
        """

        if check_freqs is not None:
            self._check_freqs = check_freqs
        if check_data is not None:
            self._check_data = check_data


    # This kept for backwards compatibility, but to be removed in 2.0 in favor of `set_check_modes`
    def set_check_data_mode(self, check_data):
        """Set check data mode, which controls if an error is raised if NaN or Inf data are added.

        Parameters
        ----------
        check_data : bool
            Whether to run in check data mode.
        """

        self.set_check_modes(check_data=check_data)


    def set_run_modes(self, debug, check_freqs, check_data):
        """Simultaneously set all run modes.

        Parameters
        ----------
        debug : bool
            Whether to run in debug mode.
        check_freqs : bool
            Whether to run in check freqs mode.
        check_data : bool
            Whether to run in check data mode.
        """

        self.set_debug_mode(debug)
        self.set_check_modes(check_freqs, check_data)


    def to_df(self, peak_org):
        """Convert and extract the model results as a pandas object.

        Parameters
        ----------
        peak_org : int or Bands
            How to organize peaks.
            If int, extracts the first n peaks.
            If Bands, extracts peaks based on band definitions.

        Returns
        -------
        pd.Series
            Model results organized into a pandas object.
        """

        return model_to_dataframe(self.get_results(), peak_org)


    def _check_width_limits(self):
        """Check and warn about peak width limits / frequency resolution interaction."""

        # Check peak width limits against frequency resolution and warn if too close
        if 1.5 * self.freq_res >= self.peak_width_limits[0]:
            print(gen_width_warning_str(self.freq_res, self.peak_width_limits[0]))


#################### new ap function with 3 options ###############################
    
    def _simple_ap_fit(self, freqs, power_spectrum):
        """
        Fit the aperiodic component of the power spectrum.

        Parameters
        ----------
        freqs : 1d array
            Frequency values for the power_spectrum, in linear scale.
        power_spectrum : 1d array
            Power values, in log10 scale.

        Returns
        -------
        
        aperiodic_params : 1d array
            Parameter estimates for aperiodic fit.
        """
        off_guess = [power_spectrum[0] if not self._ap_guess[0] else self._ap_guess[0]]
        kne_guess = [self._ap_guess[1]] if self.aperiodic_mode == 'knee_1exp' else []
        exp_guess = [np.abs((self.power_spectrum[-1] - self.power_spectrum[0]) /
                            (np.log10(self.freqs[-1]) - np.log10(self.freqs[0])))
                     if not self._ap_guess[2] else self._ap_guess[2]]
        if self.aperiodic_mode == 'fixed' or self.aperiodic_mode == 'knee_1exp':
            if self.aperiodic_mode == 'knee_1exp':
                ap_bounds = tuple(bound[0:3] for bound in self._ap_bounds)  
            elif self.aperiodic_mode == 'fixed':
                ap_bounds = tuple(bound[0:2] for bound in self._ap_bounds)

            # Collect together guess parameters
            guess = np.array(off_guess + kne_guess + exp_guess)
            print(guess.shape)
            print(ap_bounds[0])
            print(ap_bounds[1])
            print(ap_bounds)
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    aperiodic_params, _ = curve_fit(get_ap_func(self.aperiodic_mode),
                                        freqs, power_spectrum, p0=guess,
                                        maxfev=self._maxfev, bounds=ap_bounds,
                                        ftol=self._tol, xtol=self._tol, gtol=self._tol,
                                        check_finite=False)
            except RuntimeError as excp:
                error_msg = "Model fitting failed due to not finding parameters in the simple aperiodic component fit."
                print(f"Guess: {guess}")
                print(f"Lower bounds: {ap_bounds[0]}")
                print(f"Upper bounds: {ap_bounds[1]}")

                raise FitError(error_msg) from excp

        if self.aperiodic_mode == 'knee':
            # Select subregion of PSD
            f_mask = np.logical_and(freqs >= self.freq_range[0], freqs <= self.freq_range[1])
            freqs = freqs[f_mask]
            power_spectrum = power_spectrum[f_mask]
            original_psd = power_spectrum.copy()  # vis

            # Calculate guess parameters
            freq_res = freqs[1] - freqs[0]

            start_index1 = round(13 / freq_res)
            end_index1 = round(46 / freq_res)
            f1 = freqs[start_index1:end_index1]
            u1 = np.log10(f1)
            p1 = power_spectrum[start_index1:end_index1]
            guess_d1 = [(p1[-1] - p1[0]) / (u1[0] - u1[-1])]
            off1 = (guess_d1[0] * u1[0]) + p1[0]

            start_index2 = round(75 / freq_res)
            end_index2 = round(120 / freq_res)
            f2 = freqs[start_index2:end_index2]
            u2 = np.log10(f2)
            p2 = power_spectrum[start_index2:end_index2]
            guess_d2 = [(p2[-1] - p2[0]) / (u2[0] - u2[-1])]
            off2 = (guess_d2[0] * u2[0] + p2[0])

            z_guess = [(off2 - off1) / (guess_d2[0] - guess_d1[0])]
            knee_off_guess = [np.log10(2) + (off1 - (guess_d1[0] * z_guess[0]))]

            guess = np.array(knee_off_guess + z_guess + guess_d1 + guess_d2)
            ap_bounds = self._ap_bounds

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                aperiodic_params, _ = curve_fit(get_ap_func(self.aperiodic_mode),
                                                freqs, power_spectrum, p0=guess,
                                                maxfev=self._maxfev, bounds=ap_bounds,
                                                ftol=self._tol, xtol=self._tol, gtol=self._tol,
                                                check_finite=False)
        except RuntimeError as excp:
            error_msg = ("Model fitting failed due to not finding parameters in "
                        "the simple aperiodic component fit.")
            raise FitError(error_msg) from excp


        return aperiodic_params

    """

    def _simple_ap_fit(self, freqs, power_spectrum):
        
        Fit the aperiodic component of the power spectrum.

        Parameters
        ----------
        freqs : 1d array
            Frequency values for the power_spectrum, in linear scale.
        power_spectrum : 1d array
            Power values, in log10 scale.

        Returns
        -------
        aperiodic_params : 1d array
            Parameter estimates for aperiodic fit.
       
        # Get the guess parameters and/or calculate from the data, as needed
        #   Note that these are collected as lists, to concatenate with or without knee later
        
        def get_initial_guess(aperiodic_mode, power_spectrum, freqs):
            off_guess = [power_spectrum[0] if not self._ap_guess[0] else self._ap_guess[0]]
            kne_guess = [self._ap_guess[1]] if aperiodic_mode == 'knee_1exp' else []
            exp_guess = [np.abs((power_spectrum[-1] - power_spectrum[0]) /
                                (np.log10(freqs[-1]) - np.log10(freqs[0])))
                        if not self._ap_guess[2] else self._ap_guess[2]]
            return np.array(off_guess + kne_guess + exp_guess)

        guess = None
        if self.aperiodic_mode in ['fixed', 'knee_1exp']:
            guess = get_initial_guess(self.aperiodic_mode, power_spectrum, freqs)
            ap_bounds = tuple(bound[0::2] for bound in self._ap_bounds)        
            
        elif self.aperiodic_mode =='knee':           
            
            freq_res = freqs[1] - freqs[0]
            start_index1 = round(13/freq_res)
            end_index1 = round(46/freq_res)
            f1 = freqs[start_index1:end_index1]
            u1 = np.log10(f1)
            p1 = power_spectrum[start_index1:end_index1]
            guess_d1 = [(p1[-1] - p1[0])/(u1[0]-u1[-1])]
            off1 = (guess_d1[0]*u1[0]) + p1[0]
            if guess_d1[0] < 0:
                guess_d1 = [0]
            start_index2 = round(75/freq_res)
            end_index2 = round(120/freq_res)
            f2 = freqs[start_index2:end_index2]
            u2 = np.log10(f2)
            p2 = power_spectrum[start_index2:end_index2]
            guess_d2 = [(p2[-1] - p2[0])/(u2[0]-u2[-1])]
            if guess_d2[0] < 0:
                guess_d2 = [0]
            off2 = (guess_d2[0]*u2[0] + p2[0])

            z_guess = [(off2 - off1)/(guess_d2[0] - guess_d1[0])]
            if z_guess[0] < 0:
                z_guess = [0]  
            knee_off_guess = [np.log10(2) + (off1 - (guess_d1[0]*z_guess[0]))]

            initial_guess = knee_off_guess - np.log10(10**(guess_d1*(np.log10(freqs)-z_guess)) + 10**(guess_d2*(np.log10(freqs)-z_guess))) # vis

            guess = np.array(knee_off_guess + z_guess + guess_d1 + guess_d2) 
            
            ap_bounds = self._ap_bounds if self.aperiodic_mode == 'knee' \
                else tuple(bound[0::2] for bound in self._ap_bounds)


        # Ignore warnings that are raised in curve_fit
        #   A runtime warning can occur while exploring parameters in curve fitting
        #     This doesn't effect outcome - it won't settle on an answer that does this
        #   It happens if / when b < 0 & |b| > x**2, as it leads to log of a negative number
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                aperiodic_params, _ = curve_fit(get_ap_func(self.aperiodic_mode),
                                                freqs, power_spectrum, p0=guess,
                                                maxfev=self._maxfev, bounds=ap_bounds,
                                                ftol=self._tol, xtol=self._tol, gtol=self._tol,
                                                check_finite=False)
        except RuntimeError as excp:
            error_msg = ("Model fitting failed due to not finding parameters in "
                            "the simple aperiodic component fit.")
            raise FitError(error_msg) from excp
        return aperiodic_params

    """

    def _robust_ap_fit(self, freqs, power_spectrum):
        """Fit the aperiodic component of the power spectrum robustly, ignoring outliers.

        Parameters
        ----------
        freqs : 1d array
            Frequency values for the power spectrum, in linear scale.
        power_spectrum : 1d array
            Power values, in log10 scale.

        Returns
        -------
        aperiodic_params : 1d array
            Parameter estimates for aperiodic fit.

        Raises
        ------
        FitError
            If the fitting encounters an error.
        """

        # Do a quick, initial aperiodic fit
        popt = self._simple_ap_fit(freqs, power_spectrum)
        initial_fit = gen_aperiodic(freqs, popt)

        # Flatten power_spectrum based on initial aperiodic fit
        flatspec = power_spectrum - initial_fit

        # Flatten outliers, defined as any points that drop below 0
        flatspec[flatspec < 0] = 0

        # Use percentile threshold, in terms of # of points, to extract and re-fit
        perc_thresh = np.percentile(flatspec, self._ap_percentile_thresh)
        perc_mask = flatspec <= perc_thresh
        freqs_ignore = freqs[perc_mask]
        spectrum_ignore = power_spectrum[perc_mask]

        # Get bounds for aperiodic fitting, dropping knee bound if not set to fit knee
        if self.aperiodic_mode == 'knee':
            ap_bounds = self._ap_bounds
        elif self.aperiodic_mode == 'knee_1exp':
            ap_bounds = tuple(bound[0:3] for bound in self._ap_bounds)  # Assuming _ap_bounds already correctly structured for 'knee_1exp'
        elif self.aperiodic_mode == 'fixed':
            ap_bounds = tuple(bound[0:2] for bound in self._ap_bounds)
        else:
            raise ValueError(f"Unknown aperiodic mode: {self.aperiodic_mode}")

        # Second aperiodic fit - using results of first fit as guess parameters
        #  See note in _simple_ap_fit about warnings
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                aperiodic_params, _ = curve_fit(get_ap_func(self.aperiodic_mode),
                                                freqs_ignore, spectrum_ignore, p0=popt,
                                                maxfev=self._maxfev, bounds=ap_bounds,
                                                ftol=self._tol, xtol=self._tol, gtol=self._tol,
                                                check_finite=False)
        except RuntimeError as excp:
            error_msg = ("Model fitting failed due to not finding "
                         "parameters in the robust aperiodic fit.")
            raise FitError(error_msg) from excp
        except TypeError as excp:
            error_msg = ("Model fitting failed due to sub-sampling "
                         "in the robust aperiodic fit.")
            raise FitError(error_msg) from excp

        return aperiodic_params




##################################################inserted#########################################################################################

    def _fit_peaks2(self, flat_iter):
        

        # Initialize matrix of guess parameters for gaussian fitting
        guess = np.empty([0, 3])
        self.std_limits =  np.empty([0, 2])


        # Find peak: Loop through, finding a candidate peak, and fitting with a guess gaussian
        #   Stopping procedures: limit on # of peaks, or relative or absolute height thresholds
        while len(guess) < self.max_n_peaks:

            # Find candidate peak - the maximum point of the flattened spectrum
            max_ind = np.argmax(flat_iter)
            max_height = flat_iter[max_ind]

            # Stop searching for peaks once height drops below height threshold
            if max_height <= self.peak_threshold * np.std(flat_iter):
                break

            # Set the guess parameters for gaussian fitting, specifying the mean and height
            guess_freq = self.freqs[max_ind]
            guess_height = max_height

            # Halt fitting process if candidate peak drops below minimum height
            if not guess_height > self.min_peak_height:
                break

            # Data-driven first guess at standard deviation
            #   Find half height index on each side of the center frequency
            half_height = 0.5 * max_height
            le_ind = next((val for val in range(max_ind - 1, 0, -1)
                           if flat_iter[val] <= half_height), None)
            ri_ind = next((val for val in range(max_ind + 1, len(flat_iter), 1)
                           if flat_iter[val] <= half_height), None)

            # Guess bandwidth procedure: estimate the width of the peak
            try:
                # Get an estimated width from the shortest side of the peak
                #   We grab shortest to avoid estimating very large values from overlapping peaks
                # Grab the shortest side, ignoring a side if the half max was not found
                short_side = min([abs(ind - max_ind) \
                    for ind in [le_ind, ri_ind] if ind is not None])

                # Use the shortest side to estimate full-width, half max (converted to Hz)
                #   and use this to estimate that guess for gaussian standard deviation
                fwhm = short_side * 2 * self.freq_res
                guess_std = compute_gauss_std(fwhm)

            except ValueError:
                # This procedure can fail (very rarely), if both left & right inds end up as None
                #   In this case, default the guess to the average of the peak width limits
                guess_std = np.mean(self.peak_width_limits)

            # Check that guess value isn't outside preset limits - restrict if so
            #   Note: without this, curve_fitting fails if given guess > or < bounds
            if guess_std < self._gauss_std_limits[0]:
                guess_std = self._gauss_std_limits[0]
            if guess_std > self._gauss_std_limits[1]:
                guess_std = self._gauss_std_limits[1]
            
            self.std_limits = np.vstack((self.std_limits, self._gauss_std_limits))
            # Collect guess parameters and subtract this guess gaussian from the data
            guess = np.vstack((guess, (guess_freq, guess_height, guess_std)))
            peak_gauss = gaussian_function(self.freqs, guess_freq, guess_height, guess_std)
            flat_iter = flat_iter - peak_gauss

        # Check peaks based on edges, and on overlap, dropping any that violate requirements
        guess, self.std_limits = self._drop_peak_cf(guess)
        guess ,self.std_limits = self._drop_peak_overlap(guess)

        # If there are peak guesses, fit the peaks, and sort results
        if len(guess) > 0:
            gaussian_params = self._fit_peak_guess(guess)
            gaussian_params = gaussian_params[gaussian_params[:, 0].argsort()]
        else:
            gaussian_params = np.empty([0, 3])

        return gaussian_params
    
########################chatgpt version#################






##################################################inserted#########################################################################################
    def _fit_peaks(self, flat_iter):
        """
        Iteratively fit peaks to flattened spectrum.

        Parameters
        ----------
        flat_iter : 1d array
        Flattened power spectrum values.

        Returns
        -------
        gaussian_params : 2d array
        Parameters that define the gaussian fit(s).
        Each row is a gaussian, as [mean, height, standard deviation].
        """
        # Define frequency ranges and corresponding parameters
        theta_range = [1, 10]
        sgamma_range = [25, 40]
        electronic_noise_range = [45, 55]
        self.std_limits = np.empty([0 , 2])
        fgamma_range =[50, 100]
        swr_range = [120, 200]
        list_of_ranges = [theta_range, sgamma_range, fgamma_range, swr_range]
        list_of_thresholds = [1, 1.25, 2, 2]  # Adjusted thresholds based on typical SNR
        list_of_peak_widths = [[0.5, 3], [2, 10], [2.5, 30], [10,100]]
        # Initialize matrix of guess parameters for gaussian fitting
        guess = np.empty([0, 3])
        # Find peak: Loop through, finding a candidate peak, and fitting with a guess gaussian
        # Stopping procedures: limit on # of peaks, or relative or absolute height thresholds
        for current_range, current_threshold, current_width in zip(list_of_ranges, list_of_thresholds, list_of_peak_widths):
            current_range_indices = (self.freqs >= current_range[0]) & (self.freqs <= current_range[1])
            current_band = flat_iter[current_range_indices]
            current_freqs = self.freqs[current_range_indices]
            i=0
            while i<1:
                # Find candidate peak - the maximum point of the flattened spectrum
                max_ind = np.argmax(current_band)
                max_height = current_band[max_ind]

                # Stop searching for peaks once height drops below height threshold
                if max_height <= current_threshold * np.std(current_band):
                    break

                # Set the guess parameters for gaussian fitting, specifying the mean and height
                guess_freq = current_freqs[max_ind]
                guess_height = max_height
              # Halt fitting process if candidate peak drops below minimum height
                if not guess_height > self.min_peak_height:
                    break          

                # Data-driven first guess at standard deviation
                # Find half height index on each side of the center frequency
                half_height = 0.5 * max_height
                le_ind = next((val for val in range(max_ind - 1, 0, -1) if current_band[val] <= half_height), None)
                ri_ind = next((val for val in range(max_ind + 1, len(current_band), 1) if current_band[val] <= half_height), None)

                # Guess bandwidth procedure: estimate the width of the peak
                try:
                    # Get an estimated width from the shortest side of the peak
                    # We grab shortest to avoid estimating very large values from overlapping peaks
                    # Grab the shortest side, ignoring a side if the half max was not found
                    short_side = min([abs(ind - max_ind) for ind in [le_ind, ri_ind] if ind is not None])

                    # Use the shortest side to estimate full-width, half max (converted to Hz)
                    # and use this to estimate that guess for gaussian standard deviation
                    fwhm = short_side * 2 * self.freq_res
                    guess_std = compute_gauss_std(fwhm)

                except ValueError:
                    # This procedure can fail (very rarely), if both left & right inds end up as None
                    # In this case, default the guess to the average of the peak width limits
                    guess_std = np.mean(current_width)

                # Check that guess value isn't outside preset limits - restrict if so
                if guess_std < current_width[0]:
                    guess_std = current_width[0]
                if guess_std > current_width[1]:
                    guess_std = current_width[1]
                self.std_limits = np.vstack((self.std_limits,current_width))
                # Collect guess parameters and subtract this guess gaussian from the data
                guess = np.vstack((guess, (guess_freq, guess_height, guess_std)))
                peak_gauss = gaussian_function(current_freqs, guess_freq, guess_height, guess_std)
                current_band = current_band - peak_gauss
                flat_iter[current_range_indices] = current_band
                i+=1

        # Check peaks based on edges, and on overlap, dropping any that violate requirements
        guess, self.std_limits = self._drop_peak_cf(guess)
        guess ,  self.std_limits  = self._drop_peak_overlap(guess)
        # If there are peak guesses, fit the peaks, and sort results
        if len(guess) > 0:
            gaussian_params = self._fit_peak_guess(guess)

            gaussian_params = gaussian_params[gaussian_params[:, 0].argsort()]
        else:
            gaussian_params = np.empty([0, 3])

        return gaussian_params

    
    
    """ 
    def _fit_peaks(self, flat_iter):
        
        Iteratively fit peaks to flattened spectrum.

        Parameters
        ----------
        flat_iter : 1d array
            Flattened power spectrum values.

        Returns
        -------
        gaussian_params : 2d array
            Parameters that define the gaussian fit(s).
            Each row is a gaussian, as [mean, height, standard deviation].
        
        theta_range = [1, 15]
        theta_harmonic_range = [9, 18]
        sgamma_range = [18, 47]
        electronic_noise_range = [45, 55]

        list_of_ranges = [electronic_noise_range, theta_range, theta_harmonic_range, sgamma_range]   
        list_of_thresholds = [4, 2, 4, 2] 
        list_of_peak_widths = [[0.5, 3], [0.5, 4], [0.5, 4], [3, 20]]
        
        # Initialize matrix of guess parameters for gaussian fitting
        guess = np.empty([0, 3])
        # Find peak: Loop through, finding a candidate peak, and fitting with a guess gaussian
        #   Stopping procedures: limit on # of peaks, or relative or absolute height thresholds
        for current_range, current_threshold, current_width in zip(list_of_ranges, list_of_thresholds, list_of_peak_widths):
            current_range_indeces = (self.freqs >= current_range[0]) & (self.freqs <= current_range[1])
            current_band = flat_iter[current_range_indeces]
            current_freqs = self.freqs[current_range_indeces]
            # Find candidate peak - the maximum point of the flattened spectrum
            max_ind = np.argmax(current_band)
            max_height = current_band[max_ind]

            # Stop searching for peaks once height drops below height threshold
            if max_height <= current_threshold * np.std(current_band):
                continue

            # Set the guess parameters for gaussian fitting, specifying the mean and height
            guess_freq = current_freqs[max_ind]
            guess_height = max_height

            # Halt fitting process if candidate peak drops below minimum height
            if not guess_height > self.min_peak_height:
                continue          

            # Data-driven first guess at standard deviation
            #   Find half height index on each side of the center frequency
            half_height = 0.5 * max_height
            le_ind = next((val for val in range(max_ind - 1, 0, -1)
                            if current_band[val] <= half_height), None)
            ri_ind = next((val for val in range(max_ind + 1, len(current_band), 1)
                            if current_band[val] <= half_height), None)

            # Guess bandwidth procedure: estimate the width of the peak
            try:
                # Get an estimated width from the shortest side of the peak
                #   We grab shortest to avoid estimating very large values from overlapping peaks
                # Grab the shortest side, ignoring a side if the half max was not found
                short_side = min([abs(ind - max_ind) \
                    for ind in [le_ind, ri_ind] if ind is not None])

                # Use the shortest side to estimate full-width, half max (converted to Hz)
                #   and use this to estimate that guess for gaussian standard deviation
                fwhm = short_side * 2 * self.freq_res
                guess_std = compute_gauss_std(fwhm)

            except ValueError:
                # This procedure can fail (very rarely), if both left & right inds end up as None
                #   In this case, default the guess to the average of the peak width limits
                guess_std = np.mean(current_width)

            # Check that guess value isn't outside preset limits - restrict if so
            #   Note: without this, curve_fitting fails if given guess > or < bounds
            if current_range == theta_range:
                if guess_freq > 6.5:
                    theta_harmonic_range[0]= 12
                    
            elif current_range == theta_harmonic_range:
                current_width[1] = 20 - guess_freq
                
            if guess_std < current_width[0]:
                guess_std = current_width[0]
            if guess_std > current_width[1]:
                guess_std = current_width[1]

            # Collect guess parameters and subtract this guess gaussian from the data
            guess = np.vstack((guess, (guess_freq, guess_height, guess_std)))
            peak_gauss = gaussian_function(current_freqs, guess_freq, guess_height, guess_std)
            current_band = current_band - peak_gauss
            flat_iter[current_range_indeces] = current_band

#######################################changed################################################################################       

        # Check peaks based on edges, and on overlap, dropping any that violate requirements
        guess = self._drop_peak_cf(guess)
        guess = self._drop_peak_overlap(guess)

        # If there are peak guesses, fit the peaks, and sort results
        if len(guess) > 0:
            gaussian_params = self._fit_peak_guess(guess)
            gaussian_params = gaussian_params[gaussian_params[:, 0].argsort()]
        else:
            gaussian_params = np.empty([0, 3])
        print(gaussian_params)
        return gaussian_params
    
    
   
   
   
   
    
    def _fit_peaks(self, flat_iter):
        
        Iteratively fit peaks to flattened spectrum.
        
        Parameters
        ----------
        flat_iter : 1d array
            Flattened power spectrum values.

        Returns
        -------
        gaussian_params : 2d array
            Parameters that define the gaussian fit(s).
            Each row is a gaussian, as [mean, height, standard deviation].
        

        # Initialize matrix of guess parameters for gaussian fitting
        guess = np.empty([0, 3])

        # Find peak: Loop through, finding a candidate peak, and fitting with a guess gaussian
        #   Stopping procedures: limit on # of peaks, or relative or absolute height thresholds
        while len(guess) < self.max_n_peaks:

            # Find candidate peak - the maximum point of the flattened spectrum
            max_ind = np.argmax(flat_iter)
            max_height = flat_iter[max_ind]

            # Stop searching for peaks once height drops below height threshold
            if max_height <= self.peak_threshold * np.std(flat_iter):
                break

            # Set the guess parameters for gaussian fitting, specifying the mean and height
            guess_freq = self.freqs[max_ind]
            guess_height = max_height

            # Halt fitting process if candidate peak drops below minimum height
            if not guess_height > self.min_peak_height:
                break

            # Data-driven first guess at standard deviation
            #   Find half height index on each side of the center frequency
            half_height = 0.5 * max_height
            le_ind = next((val for val in range(max_ind - 1, 0, -1)
                           if flat_iter[val] <= half_height), None)
            ri_ind = next((val for val in range(max_ind + 1, len(flat_iter), 1)
                           if flat_iter[val] <= half_height), None)

            # Guess bandwidth procedure: estimate the width of the peak
            try:
                # Get an estimated width from the shortest side of the peak
                #   We grab shortest to avoid estimating very large values from overlapping peaks
                # Grab the shortest side, ignoring a side if the half max was not found
                short_side = min([abs(ind - max_ind) \
                    for ind in [le_ind, ri_ind] if ind is not None])

                # Use the shortest side to estimate full-width, half max (converted to Hz)
                #   and use this to estimate that guess for gaussian standard deviation
                fwhm = short_side * 2 * self.freq_res
                guess_std = compute_gauss_std(fwhm)

            except ValueError:
                # This procedure can fail (very rarely), if both left & right inds end up as None
                #   In this case, default the guess to the average of the peak width limits
                guess_std = np.mean(self.peak_width_limits)

            # Check that guess value isn't outside preset limits - restrict if so
            #   Note: without this, curve_fitting fails if given guess > or < bounds
            if guess_std < self._gauss_std_limits[0]:
                guess_std = self._gauss_std_limits[0]
            if guess_std > self._gauss_std_limits[1]:
                guess_std = self._gauss_std_limits[1]

            # Collect guess parameters and subtract this guess gaussian from the data
            guess = np.vstack((guess, (guess_freq, guess_height, guess_std)))
            peak_gauss = gaussian_function(self.freqs, guess_freq, guess_height, guess_std)
            flat_iter = flat_iter - peak_gauss

        # Check peaks based on edges, and on overlap, dropping any that violate requirements
        guess = self._drop_peak_cf(guess)
        guess = self._drop_peak_overlap(guess)

        # If there are peak guesses, fit the peaks, and sort results
        if len(guess) > 0:
            gaussian_params = self._fit_peak_guess(guess)
            gaussian_params = gaussian_params[gaussian_params[:, 0].argsort()]
        else:
            gaussian_params = np.empty([0, 3])

        return gaussian_params
    """

    def _fit_peak_guess(self, guess):
        """
        Fits a group of peak guesses with a fit function.

        Parameters
        ----------
        guess : 2d array, shape=[n_peaks, 3]
            Guess parameters for gaussian fits to peaks, as gaussian parameters.

        Returns
        -------
        gaussian_params : 2d array, shape=[n_peaks, 3]
            Parameters for gaussian fits to peaks, as gaussian parameters.
        """

        # Set the bounds for CF, enforce positive height value, and set bandwidth limits
        #   Note that 'guess' is in terms of gaussian std, so +/- BW is 2 * the guess_gauss_std
        #   This set of list comprehensions is a way to end up with bounds in the form:
        #     ((cf_low_peak1, height_low_peak1, bw_low_peak1, *repeated for n_peaks*),
        #      (cf_high_peak1, height_high_peak1, bw_high_peak, *repeated for n_peaks*))
        #     ^where each value sets the bound on the specified parameter
        num_peaks = guess.shape[0]
    
        # Initialize self.std_limits if it is empty
        print("std_limits are, " , self.std_limits)
                # Debugging output to check the input sizes
        if self.std_limits.shape[0] != num_peaks:
            raise ValueError("The number of peaks in 'guess' does not match the number of std limits provided.")
        # Set the bounds for CF, enforce positive height value, and set bandwidth limits
        lo_bound = [[peak[0] - self._cf_bound * peak[2], 0, std_lim[0]] ## Default was 2* self.cf_bound
                    for peak, std_lim in zip(guess, self.std_limits)]
        hi_bound = [[peak[0] + self._cf_bound * peak[2], np.inf, std_lim[1]]
                    for peak, std_lim in zip(guess, self.std_limits)]

        
        

        # Check that CF bounds are within frequency range
        lo_bound = [bound if bound[0] > self.freq_range[0] else \
            [self.freq_range[0], *bound[1:]] for bound in lo_bound]
        hi_bound = [bound if bound[0] < self.freq_range[1] else \
            [self.freq_range[1], *bound[1:]] for bound in hi_bound]

        # Unpacks the embedded lists into flat tuples
        gaus_param_bounds = (tuple(item for sublist in lo_bound for item in sublist),
                            tuple(item for sublist in hi_bound for item in sublist))

        # Flatten guess, for use with curve fit
        guess_flat = np.ndarray.flatten(guess)

        # Check if the initial guess is within the bounds
        for i, g in enumerate(guess_flat):
            if not (gaus_param_bounds[0][i] <= g <= gaus_param_bounds[1][i]):
                raise ValueError(f"Initial guess {g} at index {i} is out of bounds: "
                                f"{gaus_param_bounds[0][i]}, {gaus_param_bounds[1][i]}")

        # Fit the peaks
        try:
            gaussian_params, _ = curve_fit(gaussian_function, self.freqs, self._spectrum_flat,
                                        p0=guess_flat, maxfev=self._maxfev, bounds=gaus_param_bounds,
                                        ftol=self._tol, xtol=self._tol, gtol=self._tol,
                                        check_finite=False, jac=jacobian_gauss)
        except RuntimeError as excp:
            error_msg = ("Model fitting failed due to not finding "
                        "parameters in the peak component fit.")
            raise FitError(error_msg) from excp
        except LinAlgError as excp:
            error_msg = ("Model fitting failed due to a LinAlgError during peak fitting. "
                        "This can happen with settings that are too liberal, leading, "
                        "to a large number of guess peaks that cannot be fit together.")
            raise FitError(error_msg) from excp

        # Re-organize params into 2d matrix
        gaussian_params = np.array(group_three(gaussian_params))
        
        return gaussian_params


    def _create_peak_params(self, gaus_params):
        """Copies over the gaussian params to peak outputs, updating as appropriate.

        Parameters
        ----------
        gaus_params : 2d array
            Parameters that define the gaussian fit(s), as gaussian parameters.

        Returns
        -------
        peak_params : 2d array
            Fitted parameter values for the peaks, with each row as [CF, PW, BW].

        Notes
        -----
        The gaussian center is unchanged as the peak center frequency.

        The gaussian height is updated to reflect the height of the peak above
        the aperiodic fit. This is returned instead of the gaussian height, as
        the gaussian height is harder to interpret, due to peak overlaps.

        The gaussian standard deviation is updated to be 'both-sided', to reflect the
        'bandwidth' of the peak, as opposed to the gaussian parameter, which is 1-sided.

        Performing this conversion requires that the model has been run,
        with `freqs`, `fooofed_spectrum_` and `_ap_fit` all required to be available.
        """

        peak_params = np.empty((len(gaus_params), 3))

        for ii, peak in enumerate(gaus_params):

            # Gets the index of the power_spectrum at the frequency closest to the CF of the peak
            ind = np.argmin(np.abs(self.freqs - peak[0]))

            # Collect peak parameter data
            peak_params[ii] = [peak[0], self.fooofed_spectrum_[ind] - self._ap_fit[ind],
                               peak[2] * 2]

        return peak_params


    def _drop_peak_cf(self, guess):
        """Check whether to drop peaks based on center's proximity to the edge of the spectrum.

        Parameters
        ----------
        guess : 2d array
            Guess parameters for gaussian peak fits. Shape: [n_peaks, 3].

        Returns
        -------
        guess : 2d array
            Guess parameters for gaussian peak fits. Shape: [n_peaks, 3].
        """

        cf_params = guess[:, 0]
        bw_params = guess[:, 2] * self._bw_std_edge

        # Check if peaks within drop threshold from the edge of the frequency range
        keep_peak = \
            (np.abs(np.subtract(cf_params, self.freq_range[0])) > bw_params) & \
            (np.abs(np.subtract(cf_params, self.freq_range[1])) > bw_params)

        # Ensure self.std_limits is a numpy array
        self.std_limits = np.array(self.std_limits)

        # Drop peaks that fail the center frequency edge criterion
        guess = guess[keep_peak]
        self.std_limits = self.std_limits[keep_peak]

        return guess, self.std_limits


    def _drop_peak_overlap(self, guess):
        """Checks whether to drop gaussians based on amount of overlap.

        Parameters
        ----------
        guess : 2d array
            Guess parameters for gaussian peak fits. Shape: [n_peaks, 3].

        Returns
        -------
        guess : 2d array
            Guess parameters for gaussian peak fits. Shape: [n_peaks, 3].
        std_limits : list of lists
            Filtered standard deviation limits corresponding to the kept peaks.

        Notes
        -----
        For any gaussians with an overlap that crosses the threshold,
        the lowest height guess Gaussian is dropped.
        """

        # Ensure guess is a numpy array
        

        # Ensure guess is a numpy array
        guess = np.array(guess)

        # Ensure std_limits is a numpy array
        self.std_limits = np.array(self.std_limits)

        # Debugging output to check the structure of guess and std_limits
        # Check if the dimensions are correct
        if guess.ndim != 2 or guess.shape[1] != 3:
            raise ValueError("Expected 'guess' to be a 2D array with shape [n_peaks, 3].")

        if self.std_limits.ndim != 2 or self.std_limits.shape[1] != 2:
            raise ValueError("Expected 'std_limits' to be a 2D array with shape [n_peaks, 2].")

        if len(guess) != len(self.std_limits):
            raise ValueError("The number of peaks in 'guess' does not match the number of std limits provided.")

        # Sort the peak guesses by increasing frequency and sort std_limits in the same order
        sorted_indices = np.argsort(guess[:, 0])
        guess = guess[sorted_indices]
        self.std_limits = self.std_limits[sorted_indices]

        # Debugging output to check the sorted guess and std_limits
        # Calculate standard deviation bounds for checking amount of overlap
        #   The bounds are the gaussian frequency +/- gaussian standard deviation
        bounds = [[peak[0] - peak[2] * self._gauss_overlap_thresh,
                    peak[0] + peak[2] * self._gauss_overlap_thresh] for peak in guess]

        # Debugging output to check the bounds
        # Loop through peak bounds, comparing current bound to that of next peak
        #   If the left peak's upper bound extends pass the right peaks lower bound,
        #   then drop the Gaussian with the lower height
        drop_inds = []
        for ind, b_0 in enumerate(bounds[:-1]):
            b_1 = bounds[ind + 1]

            # Check if bound of current peak extends into next peak
            if b_0[1] > b_1[0]:
                # If so, get the index of the gaussian with the lowest height (to drop)
                drop_inds.append([ind, ind + 1][np.argmin([guess[ind][1], guess[ind + 1][1]])])

        # Drop any peaks guesses that overlap too much, based on threshold
        keep_peak = np.array([ind not in drop_inds for ind in range(len(guess))], dtype=bool)
        guess = guess[keep_peak]
        self.std_limits = self.std_limits[keep_peak]
        # Check if the dimensions are correct
        if guess.ndim != 2 or guess.shape[1] != 3:
            raise ValueError("Expected 'guess' to be a 2D array with shape [n_peaks, 3].")

        if self.std_limits.ndim != 2 or self.std_limits.shape[1] != 2:
            raise ValueError("Expected 'std_limits' to be a 2D array with shape [n_peaks, 2].")

        if len(guess) != len(self.std_limits):
            raise ValueError("The number of peaks in 'guess' does not match the number of std limits provided.")

        ##############edit this return later you don't need to do this #################
        return guess, self.std_limits 



    def _calc_r_squared(self):
        """Calculate the r-squared goodness of fit of the model, compared to the original data."""

        r_val = np.corrcoef(self.power_spectrum, self.fooofed_spectrum_)
        self.r_squared_ = r_val[0][1] ** 2


    def _calc_error(self, metric=None):
        """Calculate the overall error of the model fit, compared to the original data.

        Parameters
        ----------
        metric : {'MAE', 'MSE', 'RMSE'}, optional
            Which error measure to calculate:
            * 'MAE' : mean absolute error
            * 'MSE' : mean squared error
            * 'RMSE' : root mean squared error

        Raises
        ------
        ValueError
            If the requested error metric is not understood.

        Notes
        -----
        Which measure is applied is by default controlled by the `_error_metric` attribute.
        """

        # If metric is not specified, use the default approach
        metric = self._error_metric if not metric else metric

        if metric == 'MAE':
            self.error_ = np.abs(self.power_spectrum - self.fooofed_spectrum_).mean()

        elif metric == 'MSE':
            self.error_ = ((self.power_spectrum - self.fooofed_spectrum_) ** 2).mean()

        elif metric == 'RMSE':
            self.error_ = np.sqrt(((self.power_spectrum - self.fooofed_spectrum_) ** 2).mean())

        else:
            error_msg = "Error metric '{}' not understood or not implemented.".format(metric)
            raise ValueError(error_msg)


    def _prepare_data(self, freqs, power_spectrum, freq_range, spectra_dim=1):
        """Prepare input data for adding to current object.

        Parameters
        ----------
        freqs : 1d array
            Frequency values for the power_spectrum, in linear space.
        power_spectrum : 1d or 2d array
            Power values, which must be input in linear space.
            1d vector, or 2d as [n_power_spectra, n_freqs].
        freq_range : list of [float, float]
            Frequency range to restrict power spectrum to. If None, keeps the entire range.
        spectra_dim : int, optional, default: 1
            Dimensionality that the power spectra should have.

        Returns
        -------
        freqs : 1d array
            Frequency values for the power_spectrum, in linear space.
        power_spectrum : 1d or 2d array
            Power spectrum values, in log10 scale.
            1d vector, or 2d as [n_power_specta, n_freqs].
        freq_range : list of [float, float]
            Minimum and maximum values of the frequency vector.
        freq_res : float
            Frequency resolution of the power spectrum.

        Raises
        ------
        DataError
            If there is an issue with the data.
        InconsistentDataError
            If the input data are inconsistent size.
        """

        # Check that data are the right types
        if not isinstance(freqs, np.ndarray) or not isinstance(power_spectrum, np.ndarray):
            raise DataError("Input data must be numpy arrays.")

        # Check that data have the right dimensionality
        if freqs.ndim != 1 or (power_spectrum.ndim != spectra_dim):
            raise DataError("Inputs are not the right dimensions.")

        # Check that data sizes are compatible
        if freqs.shape[-1] != power_spectrum.shape[-1]:
            raise InconsistentDataError("The input frequencies and power spectra "
                                        "are not consistent size.")

        # Check if power values are complex
        if np.iscomplexobj(power_spectrum):
            raise DataError("Input power spectra are complex values. "
                            "FOOOF does not currently support complex inputs.")

        # Force data to be dtype of float64
        #   If they end up as float32, or less, scipy curve_fit fails (sometimes implicitly)
        if freqs.dtype != 'float64':
            freqs = freqs.astype('float64')
        if power_spectrum.dtype != 'float64':
            power_spectrum = power_spectrum.astype('float64')

        # Check frequency range, trim the power_spectrum range if requested
        if freq_range:
            freqs, power_spectrum = trim_spectrum(freqs, power_spectrum, freq_range)

        # Check if freqs start at 0 and move up one value if so
        #   Aperiodic fit gets an inf if freq of 0 is included, which leads to an error
        if freqs[0] == 0.0:
            freqs, power_spectrum = trim_spectrum(freqs, power_spectrum, [freqs[1], freqs.max()])
            if self.verbose:
                print("\nFOOOF WARNING: Skipping frequency == 0, "
                      "as this causes a problem with fitting.")

        # Calculate frequency resolution, and actual frequency range of the data
        freq_range = [freqs.min(), freqs.max()]
        freq_res = freqs[1] - freqs[0]

        # Log power values
        power_spectrum = np.log10(power_spectrum)

        ## Data checks - run checks on inputs based on check modes

        if self._check_freqs:
            # Check if the frequency data is unevenly spaced, and raise an error if so
            freq_diffs = np.diff(freqs)
            if not np.all(np.isclose(freq_diffs, freq_res)):
                raise DataError("The input frequency values are not evenly spaced. "
                                "The model expects equidistant frequency values in linear space.")
        if self._check_data:
            # Check if there are any infs / nans, and raise an error if so
            if np.any(np.isinf(power_spectrum)) or np.any(np.isnan(power_spectrum)):
                error_msg = ("The input power spectra data, after logging, contains NaNs or Infs. "
                             "This will cause the fitting to fail. "
                             "One reason this can happen is if inputs are already logged. "
                             "Inputs data should be in linear spacing, not log.")
                raise DataError(error_msg)

        return freqs, power_spectrum, freq_range, freq_res


    def _add_from_dict(self, data):
        """Add data to object from a dictionary.

        Parameters
        ----------
        data : dict
            Dictionary of data to add to self.
        """

        # Reconstruct object from loaded data
        for key in data.keys():
            setattr(self, key, data[key])


    def _check_loaded_results(self, data):
        """Check if results have been added and check data.

        Parameters
        ----------
        data : dict
            A dictionary of data that has been added to the object.
        """

        # If results loaded, check dimensions of peak parameters
        #   This fixes an issue where they end up the wrong shape if they are empty (no peaks)
        if set(OBJ_DESC['results']).issubset(set(data.keys())):
            self.peak_params_ = check_array_dim(self.peak_params_)
            self.gaussian_params_ = check_array_dim(self.gaussian_params_)


    def _check_loaded_settings(self, data):
        """Check if settings added, and update the object as needed.

        Parameters
        ----------
        data : dict
            A dictionary of data that has been added to the object.
        """

        # If settings not loaded from file, clear from object, so that default
        # settings, which are potentially wrong for loaded data, aren't kept
        if not set(OBJ_DESC['settings']).issubset(set(data.keys())):

            # Reset all public settings to None
            for setting in OBJ_DESC['settings']:
                setattr(self, setting, None)

            # If aperiodic params available, infer whether knee fitting was used,
            if not np.all(np.isnan(self.aperiodic_params_)):
                self.aperiodic_mode = infer_ap_func(self.aperiodic_params_)

        # Reset internal settings so that they are consistent with what was loaded
        #   Note that this will set internal settings to None, if public settings unavailable
        self._reset_internal_settings()


    def _regenerate_freqs(self):
        """Regenerate the frequency vector, given the object metadata."""

        self.freqs = gen_freqs(self.freq_range, self.freq_res)


    def _regenerate_model(self):
        """Regenerate model fit from parameters."""

        self.fooofed_spectrum_, self._peak_fit, self._ap_fit = gen_model(
            self.freqs, self.aperiodic_params_, self.gaussian_params_, return_components=True)
