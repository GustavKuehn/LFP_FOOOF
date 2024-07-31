CA1_paths = {'F:/MSc content/work/for Hussin/TT16_acrossDays/downsampled/TT16_Cont_10-26_downsampled.mat';
            'F:/MSc content/work/for Hussin/TT11_acrossDays/downsampled/TT11_Cont_10-26_downsampled.mat';
            'F:/MSc content/work/for Hussin/TT12_acrossDays/downsampled/TT12_Cont_10-27_downsampled.mat';
            'F:/MSc content/work/for Hussin/TT10_acrossDays/downsampled/TT10_Cont_10-27_downsampled.mat';
            'F:/MSc content/work/for Hussin/TT9_acrossDays/downsampled/TT9_Cont_10-26_downsampled.mat';
            'F:\MSc content\work\Dor\TT1\TT1_Cont_04-02_downsampled.mat';
            'F:\MSc content\work\Dor\TT11\TT11_Cont_04-02_downsampled.mat'; 
            'F:\MSc content\work\Dor\TT12\TT12_Cont_03-29_downsampled.mat';
            'F:\MSc content\work\Dor\TT16\TT16_Cont_03-25_downsampled.mat';
            
            
            };

DG_paths = {'F:/MSc content/work/for Hussin/TT16_acrossDays/downsampled/TT16_Cont_11-09_downsampled.mat';
    'F:/MSc content/work/for Hussin/TT14_acrossDays/downsampled/TT14_Cont_11-06_downsampled.mat';
    'F:/MSc content/work/for Hussin/TT13_acrossDays/downsampled/TT13_Cont_11-09_downsampled.mat';
    'F:/MSc content/work/for Hussin/TT12_acrossDays/downsampled/TT12_Cont_11-14_downsampled.mat';
    'F:/MSc content/work/for Hussin/TT11_acrossDays/downsampled/TT11_Cont_11-08_downsampled.mat';
    'F:/MSc content/work/for Hussin/TT10_acrossDays/downsampled/TT10_Cont_11-09_downsampled.mat';
    'F:/MSc content/work/for Hussin/TT4_acrossDays/downsampled/TT4_Cont_11-08_downsampled.mat';
    'F:/MSc content/work/for Hussin/TT9_acrossDays/downsampled/TT9_Cont_11-14_downsampled.mat';
    'F:\MSc content\work\Dor\TT1\TT1_Cont_04-10_downsampled.mat';
    'F:\MSc content\work\Dor\TT3\TT3_Cont_04-09_downsampled.mat'; 
    'F:\MSc content\work\Dor\TT5\TT5_Cont_03-27_downsampled.mat';
    'F:\MSc content\work\Dor\TT9\TT9_Cont_04-09_downsampled.mat';
    'F:\MSc content\work\Dor\TT11\TT11_Cont_04-12_downsampled.mat';
    'F:\MSc content\work\Dor\TT12\TT12_Cont_04-10_downsampled.mat';
    'F:\MSc content\work\Dor\TT16\TT16_Cont_04-05_downsampled.mat';
    };

notch_freq = 50;
lowpass_freq = 300;
window_size = 1000;
notch_range= [48,53]; 
harmonics = {[12,19] ,[48,53] , [148,151]};
f1=1; 
f2=200;
% Run FOOOF 
%for i = 1:3
ranges = [1,500];
settings1.peak_width_limits = [2,100];
settings1.max_n_peaks = 1;
settings1.peak_threshold = 0.2;
settings.peak_width_limits = [0.5,100];
settings.max_n_peaks = 3;
settings.aperiodic_mode = 'knee_flat'; 

CA1_ap = cell(size(CA1_paths,1),4);

for i = 1:size(CA1_paths,1)

    load(CA1_paths{i,:})
    electrode_idx= 0;
    dt = mean(diff(timestamps_downsample));
    fs = 1/dt;

    electrode_idx = maxpower(tetrode_data_downsample, window_size , fs, f1, f2);
    %disp('best electrodes are being determined...')
    disp(electrode_idx)
    filtered_electode_tt= preProcess(tetrode_data_downsample(:,electrode_idx),fs,lowpass_freq);
    [freq, new_psd]= detect_notches_and_harmonics(filtered_electode_tt, window_size , harmonics, fs);
    fooof_results = doAnalysis(freq, new_psd , ranges , settings);
    fooof_plot(fooof_results)
    title(['File: ', CA1_paths{i,:}]);

    % Convert aperiodic_params to a string
    aperiodic_params_str = sprintf('%.2f ', fooof_results.aperiodic_params);
    
    % Add a text box with the aperiodic parameters
    annotation('textbox', [0.15, 0.8, 0.1, 0.1], 'String', ['Aperiodic Params: ', aperiodic_params_str], 'FitBoxToText', 'on');

    CA1_ap{i,1} = fooof_results.aperiodic_params(1);
    CA1_ap{i,2} = fooof_results.aperiodic_params(2);
    CA1_ap{i,3} = fooof_results.aperiodic_params(3);
    CA1_ap{i,4} = fooof_results.aperiodic_params(4);

end

filename = 'CasperandDor_CA1_ap.mat';
save(filename, 'CA1_ap')


DG_ap = cell(size(DG_paths,1),4);

for i = 1:size(DG_paths,1)

    load(DG_paths{i,:})
    electrode_idx= 0;
    dt = mean(diff(timestamps_downsample));
    fs = 1/dt;
    electrode_idx = maxpower(tetrode_data_downsample, window_size , fs, f1, f2);
    %disp('best electrodes are being determined...')
    disp(electrode_idx)
    filtered_electode_tt= preProcess(tetrode_data_downsample(:,electrode_idx),fs,lowpass_freq);
    [freq, new_psd]= detect_notches_and_harmonics(filtered_electode_tt, window_size , harmonics, fs);
    fooof_results = doAnalysis(freq, new_psd , ranges , settings);
    fooof_plot(fooof_results)
    title(['File: ', DG_paths{i,:}]);
    % Cnvert aperiodic_params to a string
    aperiodic_params_str = sprintf('%.2f ', fooof_results.aperiodic_params);
    
    % Add a text box with the aperiodic parameters
    annotation('textbox', [0.15, 0.8, 0.1, 0.1], 'String', ['Aperiodic Params: ', aperiodic_params_str], 'FitBoxToText', 'on');

    DG_ap{i,1} = fooof_results.aperiodic_params(1);
    DG_ap{i,2} = fooof_results.aperiodic_params(2);
    DG_ap{i,3} = fooof_results.aperiodic_params(3);
    DG_ap{i,4} = fooof_results.aperiodic_params(4);

end

filename = 'CasperandDor_DG_ap.mat'
save(filename , 'DG_ap')

data1 = load('CasperandDor_CA1_ap.mat'); % Replace 'file1.mat' with the actual file name
data2 = load('CasperandDor_DG_ap.mat'); % Replace 'file2.mat' with the actual file name

% Assuming the data is stored in variables named 'mat1' and 'DG'
CA1 = data1.CA1_ap; % Change 'mat1' to the actual variable name in the file
DG = data2.DG_ap; % Change 'DG' to the actual variable name in the file

CA1_numeric = cell2mat(CA1);
DG_numeric = cell2mat(DG);

% Number of columns (parameters)
% Number of columns (parameters)
num_columns = size(CA1, 2);


p_values = zeros(1, num_columns);
for col = 1:num_columns
    [~, p_values(col)] = ttest2(CA1_numeric(:, col), DG_numeric(:, col));
end

% Plotting
figure;

% Parameters names and labels
params = {'knee offset', 'knee log frequency', 'exponent 1', 'exponent 2'};

% Group labels
group_labels = {'CA1', 'DG'};

% Combine data for boxplot and scatter plot
combined_data = [CA1_numeric; DG_numeric];
group = [ones(size(CA1_numeric, 1), 1); 2 * ones(size(DG_numeric, 1), 1)];

% Plot knee offset and knee log frequency in separate subplots
for col = 1:2
    subplot(1, 3, col);
    
    % Boxplot with notches, all black
    boxplot(combined_data(:, col), group, 'Notch', 'on', 'Labels', group_labels, 'Colors', 'k');
    hold on;
    
    % Overlay scatter points
    scatter(repmat(1, size(CA1_numeric, 1), 1), CA1_numeric(:, col), 'r', 'filled');
    scatter(repmat(2, size(DG_numeric, 1), 1), DG_numeric(:, col), 'b', 'filled');
    
    title(['Parameter: ', params{col}, ' (p = ', num2str(p_values(col)), ')']);
    ylabel(['Parameter ', params{col}]);
    xlabel('Group');
    xticks([1 2]);
    xticklabels(group_labels);
    hold off;
    
    % Set y-axis scale
    ax = gca;
    ax.YMinorTick = 'on';
    ax.YTick = round(min(ax.YLim),2):0.1:round(max(ax.YLim),2); % Set step size for y-ticks
end

% Plot exponents 1 and 2 in the same subplot
subplot(1, 3, 3);

% Plot exponent 1
boxplot(combined_data(:, 3), group, 'Notch', 'on', 'Colors', 'k', 'Positions', [1, 2]);
hold on;
scatter(repmat(1, size(CA1_numeric, 1), 1), CA1_numeric(:, 3), 'r', 'o', 'filled');
scatter(repmat(2, size(DG_numeric, 1), 1), DG_numeric(:, 3), 'b', 'o', 'filled');

% Plot exponent 2
boxplot(combined_data(:, 4), group, 'Notch', 'on', 'Colors', 'k', 'Positions', [3, 4]);
scatter(repmat(3, size(CA1_numeric, 1), 1), CA1_numeric(:, 4), 'r', '^', 'filled');
scatter(repmat(4, size(DG_numeric, 1), 1), DG_numeric(:, 4), 'b', '^', 'filled');

title(['Exponents 1 & 2 (p1 = ', num2str(p_values(3)), ', p2 = ', num2str(p_values(4)), ')']);
xlabel('Group');
ylabel('Exponent Value');
xticks([1 2 3 4]);
xticklabels({'CA1 (Exp 1)', 'DG (Exp 1)', 'CA1 (Exp 2)', 'DG (Exp 2)'});
hold off;

% Set y-axis scale
ax = gca;
ax.YMinorTick = 'on';
ax.YTick = 0.1 :0.1:3.5; % Set step size for y-ticks

% Display p-values
disp('P-values for each parameter:');
disp(p_values);

%{

% Perform t-tests and store p-values
p_values = zeros(1, num_columns);
for col = 1:num_columns
    [~, p_values(col)] = ttest(CA1_numeric(:, col), DG_numeric(:, col));
end

% Plotting
figure;

params = {'knee offset' , 'knee log frequency', 'exponent 1', 'exponent 2'};
% Scatter plots
for col = 1:num_columns
    subplot(2, num_columns, col);
    scatter(ones(size(CA1_numeric, 1), 1), CA1_numeric(:, col), 'r', 'filled'); hold on;
    scatter(2 * ones(size(DG_numeric, 1), 1), DG_numeric(:, col), 'b', 'filled'); hold off;
    title(['Parameter ', params{col}]);
    xlabel('Group');
    ylabel(['Parameter ', params{col}]);
    xticks([1 2]);
    xticklabels({'Group 1', 'Group 2'});
    legend('Group 1', 'Group 2');
end

% Box plots
for col = 1:num_columns
    subplot(2, num_columns, num_columns + col);
    boxplot([CA1_numeric(:, col); DG_numeric(:, col)], [ones(size(CA1_numeric, 1), 1); 2 * ones(size(DG_numeric, 1), 1)]);
    title(['Parameter ', params{col}, ' (p = ', num2str(p_values(col)), ')']);
    xlabel('Group');
    ylabel(['Parameter ', params{col}]);
    xticks([1 2]);
    xticklabels({'Group 1', 'Group 2'});
end

% Display p-values
disp('P-values for each parameter:');
disp(p_values);
%}










